from __future__ import annotations

import struct

import fmsave
from fmsave import SquadRuleBound, SquadRuleKind, SquadRuleMatchKind, SquadRuleSet
from fmsave._checks import check_squad_rules
from fmsave._layouts import fm26_26_3_2
from fmsave._reader_stats import SquadRuleStats
from fmsave.readers.squad_rules import match_competition, read_squad_rules

LAYOUT = fm26_26_3_2.SQUAD_RULES


def tag(name: str) -> bytes:
    return name.encode("ascii")[::-1]


def u8(name: str, value: int) -> bytes:
    return tag(name) + bytes((1, 0x11, value))


def u16(name: str, value: int) -> bytes:
    return tag(name) + b"\x01\x12" + struct.pack("<H", value)


def u32(name: str, value: int) -> bytes:
    return tag(name) + b"\x01\x01" + struct.pack("<I", value)


def text(name: str, value: str) -> bytes:
    raw = value.encode()
    return tag(name) + b"\x01\x1a" + struct.pack("<I", len(raw)) + raw


def record(name: str | bytes | None, *fields: bytes) -> bytes:
    head = b"" if name is None else name if isinstance(name, bytes) else tag(name)
    return head + b"\x01\x0a" + struct.pack("<I", len(fields)) + b"".join(fields)


def array(name: str, *elements: bytes) -> bytes:
    return tag(name) + b"\x01\x0b" + struct.pack("<I", len(elements)) + b"".join(elements)


def rule(kind: int, bound: int, value: int, **seasons: int) -> bytes:
    code = u8("type", kind) if kind < 256 else u16("type", kind)
    fields = [code, u8("cmty", bound), u8("valu", value)]
    if "first" in seasons:
        fields.append(u16("styr", seasons["first"]))
    if "last" in seasons:
        fields.append(u16("enyr", seasons["last"]))
    return record(None, *fields)


def rule_set(
    name: str, *rules: bytes, levels: tuple[int, int] | None = None, comp: int | None = None
) -> bytes:
    fields = [text("name", name)]
    if levels is not None:
        fields += [u8("hidl", levels[0]), u8("lwdl", levels[1])]
    if comp is not None:
        fields.append(record("comp", u32("comp", comp)))
    fields.append(array("sqsr", *rules))
    return record(None, *fields)


def division(competition: int, groups: int = 1) -> bytes:
    fields = [record("comp", u32("comp", competition))]
    if groups > 1:
        fields.append(u8("nccc", groups))
    return record(None, *fields)


def team_counts(*competitions: int) -> bytes:
    return array("retm", *(record(None, record("comp", u32("comp", c))) for c in competitions))


TOP, SECOND, THIRD, WHITE, RED = 130286, 463479, 463481, 463485, 463486

SETS = array(
    "fxrl",
    rule_set(
        "PREMIER",
        rule(21, 255, 14),
        rule(368, 255, 12, last=2025),
        rule(368, 255, 10, first=2026),
        rule(9, 255, 28),
        levels=(0, 0),
    ),
    rule_set("Turkish 2. League", rule(21, 255, 0), levels=(2, 2)),
    rule_set("Amateur", rule(21, 255, 4), levels=(2, 255)),
    rule_set("Non foreign list", comp=SECOND),
)
NATION = record(
    struct.pack("<I", 3),  # a binary tag, as the nation record carries
    u8("ftye", 10),
    record("nati", u32("id  ", 0x6E617469), u16("Nnat", 799)),
    array("dvlv", division(TOP), division(SECOND), division(THIRD, groups=2)),
    team_counts(TOP, SECOND, THIRD, WHITE, RED),
    u8("dfdl", 1),
)
GAME_DB = (
    bytes(32)
    + text("file", "tur_rules")
    + SETS
    + u8("pspd", 40)
    + NATION
    + text("file", "tur_comps")
    + record(None, u8("ftye", 1), SETS)  # the reserve copy, nested, after the division list
    + bytes(16)
    + tag("fxrl")
    + b"\x01\x0b"
    + struct.pack("<I", 0)  # an empty list: not a rule-set list
    + tag("fxrl")
    + b"\x01\x02"
    + b"lrxf"  # the same tag as a plain id value, as competition files use it
)


def read() -> tuple[list[SquadRuleSet], SquadRuleStats]:
    return read_squad_rules(GAME_DB, LAYOUT)


def test_sets_reach_their_nations_division_levels_and_groups() -> None:
    rows, stats = read()

    assert [row.name for row in rows] == [
        "PREMIER",
        "Turkish 2. League",
        "Amateur",
        "Non foreign list",
    ]
    premier, second_league, amateur, non_foreign = rows
    assert premier.nation_id == 799
    assert premier.division_database_ids == (TOP,)
    assert premier.group_database_ids == ()
    assert second_league.division_database_ids == (THIRD,)
    assert second_league.group_database_ids == (WHITE, RED)
    assert amateur.division_database_ids == (THIRD,)  # 255: every level from 2 down
    assert non_foreign.competition_database_id == SECOND
    assert non_foreign.division_database_ids == ()
    # The second copy pairs backward with the same division list and is dropped as a repeat.
    assert stats.set_lists == 2
    assert (stats.levelled_set_lists, stats.levelled_set_lists_paired) == (2, 2)
    assert (stats.sets, stats.rules, stats.unknown_kinds) == (4, 6, 2)


def test_a_rule_reads_its_code_bound_value_and_seasons() -> None:
    premier = read()[0][0]

    foreign, before, after, squad = premier.rules
    assert foreign.kind.label is SquadRuleKind.FOREIGN_PLAYERS
    assert foreign.bound is not None and foreign.bound.label is SquadRuleBound.MAXIMUM
    assert foreign.value == 14
    assert (before.kind.label, before.kind.raw) == (SquadRuleKind.UNKNOWN, 368)
    assert (before.first_season, before.last_season) == (None, 2025)
    assert squad.kind.label is SquadRuleKind.SQUAD_SIZE
    assert [rule.value for rule in premier.rules_in(2025)] == [14, 12, 28]
    assert [rule.value for rule in premier.rules_in(2026)] == [14, 10, 28]
    assert after.applies_in(2030)


def test_a_group_matches_after_divisions_and_the_narrowest_set_first() -> None:
    rows, _ = read()

    red = match_competition(rows, RED)
    assert [(match.matched_by, match.rule_set.name) for match in red] == [
        (SquadRuleMatchKind.GROUP, "Turkish 2. League"),
        (SquadRuleMatchKind.GROUP, "Amateur"),
    ]
    second = match_competition(rows, SECOND)
    assert [(match.matched_by, match.rule_set.name) for match in second] == [
        (SquadRuleMatchKind.COMPETITION, "Non foreign list"),
    ]
    assert match_competition(rows, 999) == ()


def test_the_check_judges_the_share_of_levelled_lists_paired() -> None:
    bounds = fm26_26_3_2.GATE_BOUNDS
    big = bounds.minimum_applies_from_bytes
    passing = SquadRuleStats(10, 8, 8, 40, 90, 70)
    failing = SquadRuleStats(10, 8, 2, 40, 90, 70)

    assert check_squad_rules(passing, bounds, big).gates[0].passed
    assert not check_squad_rules(failing, bounds, big).gates[0].passed
    assert not check_squad_rules(failing, bounds, 10).gates[0].applied


def test_squad_rules_are_exported() -> None:
    assert fmsave.SquadRuleSet is SquadRuleSet
    assert fmsave.field_status(fmsave.SquadRule, "kind") == "unconfirmed"
    assert fmsave.field_status(SquadRuleSet, "group_database_ids") == "unconfirmed"
    assert fmsave.field_status(SquadRuleSet, "division_database_ids") == "verified"
