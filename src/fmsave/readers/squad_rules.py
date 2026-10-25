"""Squad registration rules, from the rules database's tagged stream in `game_db`.

Each nation's rules file holds an array of rule-set records, and the nation's own record holds
the array of its division levels; `SquadRuleLayout` sets out both shapes and the grammar of the
stream. The reader finds every rule-set array by its tag, keeps only arrays of records (the same
tag also names plain id values in competition files), and ties each to the division array in
its own unbroken run of tagged values: first forward from the sets, then back, accepting a
division array behind the sets only when a strict walk from it reaches them.

**Why not by file name or nation id.** Both were measured and both fail across database builds.
A 14-season save's database stores several nations' rules (England's, Spain's, Türkiye's)
without a file name, so the name before a list belongs to another nation; and the nation id is
stored three ways, one of them a string in another id space. The run is the one thing every
build keeps: on a fresh 2025/26 database and a 14-season one every named nation list pairs
this way with the division array of its own nation, checked by the names of the competitions
it reaches.

Repeated copies are dropped. A nation's rules are stored twice (once in its rules file and once
in its reserve-competition file), byte for byte the same, so a set equal in every field to one
already kept for the same division array adds nothing a caller can see.
"""

from __future__ import annotations

import bisect
import re
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from fmsave._frozen import FrozenMapping
from fmsave._layouts import SquadRuleLayout, find_layout
from fmsave._reader_stats import SquadRuleStats
from fmsave.models.common import CodedValue
from fmsave.models.squad_rules import (
    SquadRule,
    SquadRuleBound,
    SquadRuleKind,
    SquadRuleMatch,
    SquadRuleMatchKind,
    SquadRuleSet,
)
from fmsave.readers._common import GAME_DB_SECTION

_UINT32 = struct.Struct("<I")
_PAIR = struct.Struct("<II")
_FLOAT = struct.Struct("<f")


def find_squad_rule_layout(schema: int | None, build: str) -> SquadRuleLayout:
    """Look up the squad-rule layout for a `game_db` schema, falling back to the build."""
    return find_layout(SquadRuleLayout, GAME_DB_SECTION, schema, build).layout


@dataclass(frozen=True, slots=True)
class _Node:
    """One value of the tagged stream: its tag ('' for an array element), type, value or
    count, children for a record or array, and where it starts and ends."""

    tag: str
    kind: int
    value: int | float | str | tuple[int, int] | None
    children: tuple[_Node, ...]
    start: int
    end: int

    def child(self, tag: str) -> _Node | None:
        for node in self.children:
            if node.tag == tag:
                return node
        return None


class _Unreadable(Exception):
    """The bytes at a position are not a value of the stream."""


def _tag_name(raw: bytes) -> str:
    if all(0x20 <= byte <= 0x7E for byte in raw):
        return raw[::-1].decode("ascii")
    return f"#{int.from_bytes(raw, 'little')}"


class _Stream:
    """A strict reader of the tagged stream over one `game_db`."""

    def __init__(self, data: bytes, layout: SquadRuleLayout) -> None:
        self.data = data
        self.layout = layout
        self.sizes = dict(layout.value_bytes)

    def read(self, position: int, tagged: bool = True) -> _Node:
        """The value at `position`; raises _Unreadable when the bytes are not one."""
        data = self.data
        layout = self.layout
        start = position
        tag = ""
        if tagged:
            if position + layout.tag_bytes > len(data):
                raise _Unreadable
            tag = _tag_name(data[position : position + layout.tag_bytes])
            position += layout.tag_bytes
        if position + 2 > len(data) or data[position] != layout.separator_value:
            raise _Unreadable
        kind = data[position + 1]
        position += 2
        size = self.sizes.get(kind)
        if size is not None:
            if position + size > len(data):
                raise _Unreadable
            value: int | float | str | tuple[int, int] | None
            if kind == layout.pair_type:
                value = _PAIR.unpack_from(data, position)
            elif kind == layout.float_type:
                value = _FLOAT.unpack_from(data, position)[0]
            elif size:
                value = int.from_bytes(data[position : position + size], "little")
            else:
                value = None
            return _Node(tag, kind, value, (), start, position + size)
        if position + 4 > len(data):
            raise _Unreadable
        count = _UINT32.unpack_from(data, position)[0]
        position += 4
        if kind == layout.string_type:
            if count > layout.max_string_bytes or position + count > len(data):
                raise _Unreadable
            text = data[position : position + count].decode("utf-8", "replace")
            return _Node(tag, kind, text, (), start, position + count)
        if kind not in (layout.record_type, layout.array_type) or count > layout.max_count:
            raise _Unreadable
        children: list[_Node] = []
        for _ in range(count):
            child = self.read(position, tagged=kind == layout.record_type)
            children.append(child)
            position = child.end
        return _Node(tag, kind, count, tuple(children), start, position)

    def read_any(self, position: int) -> _Node:
        """A tagged value at `position`, or failing that an untagged one."""
        try:
            return self.read(position)
        except _Unreadable:
            return self.read(position, tagged=False)

    def run(self, position: int, end: int) -> list[_Node]:
        """The values from `position` until the first byte that is not one, or `end`."""
        nodes: list[_Node] = []
        while position < end:
            try:
                node = self.read_any(position)
            except _Unreadable:
                break
            nodes.append(node)
            position = node.end
        return nodes


def _integer(node: _Node | None) -> int | None:
    if node is None:
        return None
    if isinstance(node.value, tuple):
        return node.value[1]
    return node.value if isinstance(node.value, int) and node.kind not in (0x0A, 0x0B) else None


def _competition_id(node: _Node, layout: SquadRuleLayout) -> int | None:
    """A competition reference: a value, or a record holding the id or its database id."""
    if node.children:
        inner = node.child(layout.database_id_tag) or node.child(layout.competition_tag)
        return None if inner is None else _integer(inner)
    return _integer(node)


@dataclass(frozen=True, slots=True)
class _Divisions:
    """A nation's division levels and what sits around them."""

    levels: tuple[int | None, ...]
    groups: tuple[tuple[int, ...], ...]
    nation_id: int | None


def _flag_name(value: int) -> str:
    return _tag_name(value.to_bytes(4, "little"))


def _read_rule(node: _Node, layout: SquadRuleLayout) -> SquadRule:
    known = {
        layout.kind_tag,
        layout.bound_tag,
        layout.value_tag,
        layout.first_season_tag,
        layout.last_season_tag,
        layout.flags_tag,
        layout.cash_tag,
        layout.currency_tag,
    }
    kind = _integer(node.child(layout.kind_tag))
    bound = _integer(node.child(layout.bound_tag))
    cash_node = node.child(layout.cash_tag)
    cash = _integer(cash_node) if cash_node is None or not cash_node.children else None
    flags: list[str] = []
    flags_node = node.child(layout.flags_tag)
    for element in flags_node.children if flags_node is not None else ():
        for leaf in element.children or (element,):
            if isinstance(leaf.value, int) and leaf.kind not in (0x0A, 0x0B):
                flags.append(_flag_name(leaf.value))
                break
    unknown = {
        child.tag: value
        for child in node.children
        if child.tag not in known and (value := _integer(child)) is not None
    }
    return SquadRule(
        kind=CodedValue.from_raw(SquadRuleKind, -1 if kind is None else kind),
        bound=None if bound is None else CodedValue.from_raw(SquadRuleBound, bound),
        value=_integer(node.child(layout.value_tag)),
        cash=cash,
        currency=_integer(node.child(layout.currency_tag)),
        first_season=_integer(node.child(layout.first_season_tag)),
        last_season=_integer(node.child(layout.last_season_tag)),
        flags=tuple(flags),
        unknown=FrozenMapping(unknown),
    )


def _read_set(node: _Node, divisions: _Divisions | None, layout: SquadRuleLayout) -> SquadRuleSet:
    known = {
        layout.name_tag,
        layout.highest_level_tag,
        layout.lowest_level_tag,
        layout.competition_tag,
        layout.rules_tag,
    }
    name_node = node.child(layout.name_tag)
    name = name_node.value if name_node is not None and isinstance(name_node.value, str) else None
    highest = _integer(node.child(layout.highest_level_tag))
    lowest = _integer(node.child(layout.lowest_level_tag))
    explicit = node.child(layout.competition_tag)
    explicit_id = None if explicit is None else _competition_id(explicit, layout)
    competitions: list[int] = []
    groups: list[int] = []
    if divisions is not None and highest is not None:
        last = len(divisions.levels) - 1
        bottom = last if lowest is None or lowest == layout.open_level else min(lowest, last)
        for level in range(highest, bottom + 1):
            level_id = divisions.levels[level]
            if level_id is not None and level_id not in competitions:
                competitions.append(level_id)
            groups.extend(divisions.groups[level])
    rules_node = node.child(layout.rules_tag)
    rules = tuple(_read_rule(rule, layout) for rule in (rules_node.children if rules_node else ()))
    unknown = {
        child.tag: value
        for child in node.children
        if child.tag not in known and (value := _integer(child)) is not None
    }
    return SquadRuleSet(
        name=name,
        nation_id=None if divisions is None else divisions.nation_id,
        highest_division_level=highest,
        lowest_division_level=lowest,
        competition_database_id=explicit_id,
        division_database_ids=tuple(competitions),
        group_database_ids=tuple(groups),
        rules=rules,
        unknown=FrozenMapping(unknown),
    )


def _divisions(stream: _Stream, node: _Node) -> _Divisions:
    """A division array with the nation value stored before it and the competition order
    after it."""
    layout = stream.layout
    levels: list[int | None] = []
    group_counts: list[int] = []
    for element in node.children:
        competition = element.child(layout.competition_tag)
        levels.append(None if competition is None else _competition_id(competition, layout))
        group_counts.append(_integer(element.child(layout.group_count_tag)) or 1)
    nation_id = _nation_before(stream, node.start)
    order: list[int] = []
    try:
        after = stream.read(node.end)
    except _Unreadable:
        after = None
    if after is not None and after.tag == layout.team_counts_tag:
        for element in after.children:
            competition = element.child(layout.competition_tag)
            competition_id = None if competition is None else _competition_id(competition, layout)
            if competition_id is not None and competition_id not in order:
                order.append(competition_id)
    groups: list[tuple[int, ...]] = []
    for level_id, count in zip(levels, group_counts, strict=True):
        if count > 1 and level_id in order:
            at = order.index(level_id)
            groups.append(tuple(order[at + 1 : at + 1 + count]))
        else:
            groups.append(())
    return _Divisions(tuple(levels), tuple(groups), nation_id)


def _nation_before(stream: _Stream, end: int) -> int | None:
    """The nation id in the value that ends where a division array starts, when numeric."""
    layout = stream.layout
    marker = layout.nation_tag[::-1].encode("ascii") + bytes((layout.separator_value,))
    position = stream.data.rfind(marker, max(0, end - layout.nation_search_bytes), end)
    if position < 0:
        return None
    try:
        node = stream.read(position)
    except _Unreadable:
        return None
    if node.end != end:
        return None
    if node.children:
        inner = node.child(layout.nation_id_tag)
        return None if inner is None or inner.kind != 0x12 else _integer(inner)
    return _integer(node) if node.kind == 0x12 else None


def _contains(node: _Node, position: int) -> bool:
    if node.start == position:
        return True
    return any(
        child.start <= position < child.end and _contains(child, position)
        for child in node.children
    )


def _find_division_array(nodes: Iterable[_Node], tag: str) -> _Node | None:
    """The first division array among values and their direct children."""
    for node in nodes:
        if node.tag == tag:
            return node
        for child in node.children:
            if child.tag == tag:
                return child
    return None


def _division_array_for(
    stream: _Stream, set_list: _Node, division_starts: Sequence[int]
) -> _Node | None:
    layout = stream.layout
    division_tag = layout.division_list_tag[:4][::-1].decode("ascii")
    end = min(len(stream.data), set_list.end + layout.forward_scan_bytes)
    found = _find_division_array(stream.run(set_list.end, end), division_tag)
    if found is not None:
        return found
    before = bisect.bisect_left(division_starts, set_list.start) - 1
    if before < 0:
        return None
    position = division_starts[before]
    while position < set_list.start:
        try:
            node = stream.read_any(position)
        except _Unreadable:
            return None
        if node.end > set_list.start:
            if not _contains(node, set_list.start):
                return None
            break
        position = node.end
    try:
        return stream.read(division_starts[before])
    except _Unreadable:
        return None


def read_squad_rules(
    game_db: bytes, layout: SquadRuleLayout
) -> tuple[list[SquadRuleSet], SquadRuleStats]:
    """Every squad rule set the rules database holds, in stored order, repeats dropped."""
    stream = _Stream(game_db, layout)
    division_starts = [
        match.start() for match in re.finditer(re.escape(layout.division_list_tag), game_db)
    ]
    divisions_by_start: dict[int, _Divisions] = {}
    rows: list[SquadRuleSet] = []
    seen: set[tuple[object, ...]] = set()
    set_lists = levelled = paired = 0
    for match in re.finditer(re.escape(layout.set_list_tag), game_db):
        try:
            set_list = stream.read(match.start())
        except _Unreadable:
            continue
        if not set_list.children or any(
            element.kind != layout.record_type for element in set_list.children
        ):
            continue
        set_lists += 1
        is_levelled = any(
            element.child(layout.highest_level_tag) is not None for element in set_list.children
        )
        division_array = _division_array_for(stream, set_list, division_starts)
        divisions: _Divisions | None = None
        if division_array is not None:
            divisions = divisions_by_start.get(division_array.start)
            if divisions is None:
                divisions = _divisions(stream, division_array)
                divisions_by_start[division_array.start] = divisions
        if is_levelled:
            levelled += 1
            paired += divisions is not None
        for element in set_list.children:
            row = _read_set(element, divisions, layout)
            key = (divisions.levels if divisions else None, _row_key(row))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    stats = SquadRuleStats(
        set_lists=set_lists,
        levelled_set_lists=levelled,
        levelled_set_lists_paired=paired,
        sets=len(rows),
        rules=sum(len(row.rules) for row in rows),
        unknown_kinds=sum(
            rule.kind.label is SquadRuleKind.UNKNOWN for row in rows for rule in row.rules
        ),
    )
    return rows, stats


def _row_key(row: SquadRuleSet) -> tuple[object, ...]:
    return (
        row.name,
        row.nation_id,
        row.highest_division_level,
        row.lowest_division_level,
        row.competition_database_id,
        row.division_database_ids,
        row.group_database_ids,
        tuple(
            (
                rule.kind.raw,
                None if rule.bound is None else rule.bound.raw,
                rule.value,
                rule.cash,
                rule.currency,
                rule.first_season,
                rule.last_season,
                rule.flags,
                tuple(sorted(rule.unknown.items())),
            )
            for rule in row.rules
        ),
        tuple(sorted(row.unknown.items())),
    )


def match_competition(
    rule_sets: Iterable[SquadRuleSet], database_id: int
) -> tuple[SquadRuleMatch, ...]:
    """The rule sets that govern one competition, by how directly they reach it.

    A set naming the competition outright comes first, then the sets covering a division level
    it sits at, then those covering a level it is a group of. Within each kind the set covering
    the fewest division levels comes first, then stored order: Türkiye's 2. League set (level 2
    only) before its amateur set (level 2 down), which also reaches the 2. League.
    """
    by_kind: Mapping[SquadRuleMatchKind, list[SquadRuleMatch]] = {
        kind: [] for kind in SquadRuleMatchKind
    }
    for rule_set in rule_sets:
        if rule_set.competition_database_id == database_id:
            kind = SquadRuleMatchKind.COMPETITION
        elif database_id in rule_set.division_database_ids:
            kind = SquadRuleMatchKind.DIVISION
        elif database_id in rule_set.group_database_ids:
            kind = SquadRuleMatchKind.GROUP
        else:
            continue
        by_kind[kind].append(SquadRuleMatch(kind, rule_set))
    return tuple(
        match
        for kind in SquadRuleMatchKind
        for match in sorted(by_kind[kind], key=lambda match: _breadth(match.rule_set))
    )


def _breadth(rule_set: SquadRuleSet) -> int:
    """The span of a set's stored division range, an open-ended one counting as its stored
    bound; a set naming no range counts as narrowest, since it names its competition."""
    highest = rule_set.highest_division_level
    lowest = rule_set.lowest_division_level
    if highest is None:
        return 0
    return (highest if lowest is None else lowest) - highest
