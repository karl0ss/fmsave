"""Squad registration rules: foreign, non-EU and home-grown quotas and squad sizes, per league.

**The rules are database content, not career state.** Every nation's rules file in the save's
rules database holds a list of named rule sets (Türkiye's are "PREMIER", "LEAGUE A", "Turkish 2.
League", ...), and each set says which division levels of that nation it governs. The nation's
own record lists the competition at each division level, so a set reaches its leagues by a
join, not by a name. A set can also name one competition outright. The same rules come out of
every save of one installed database, whichever club is managed; a database update can change
them, and does: a 2025 database and a 2026 one differ in most nations' files.

**A rule is a code, a bound and a number.** `kind` is the game's rule code; only the codes
pinned against well-known real rules carry a label (see `SquadRuleKind`), and every other code
keeps its raw number under the UNKNOWN label. `bound` says whether `value` is a minimum or a
maximum, which is how the game words every squad rule ("Minimum of N ...", "Maximum of N ...").
A rule can be limited to a range of seasons by `first_season` and `last_season`: Türkiye's
Super League lowers one limit from 12 to 10 from 2026, stored as two rules.

**Who counts as foreign or non-EU is not here.** That depends on a player's nationalities and on
which nations belong to which union, which is nation-level data no reader decodes yet.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import ClassVar

from fmsave._status import register_field_statuses
from fmsave.models.common import CodedValue


class SquadRuleKind(IntEnum):
    """What a squad rule counts, for the codes pinned against real rules.

    Each label rests on a code taking a real league's well-known number in the 2026 database,
    not on an in-game screen, so the labels are unconfirmed:

    - SQUAD_SIZE (9): 25 for the Premier League, La Liga and Serie A, 28 for Türkiye's Super
      League.
    - NON_EU_PLAYERS (18): at most 3 in La Liga, at most 4 in Ligue 1.
    - FOREIGN_PLAYERS (21): at most 14 in Türkiye's Super League, 8 in its 1. League and 0 in its
      2. League.
    - CLUB_TRAINED_PLAYERS (38): at least 4 in the Bundesliga.
    - LOCALLY_TRAINED_PLAYERS (39): at least 8 in the Bundesliga and in Serie A.
    - HOME_GROWN_PLAYERS (146): at least 8 in the Premier League.
    - NATIONALS_UNDER_CONTRACT (212): at least 12 in the Bundesliga.
    """

    UNKNOWN = -1
    SQUAD_SIZE = 9
    NON_EU_PLAYERS = 18
    FOREIGN_PLAYERS = 21
    CLUB_TRAINED_PLAYERS = 38
    LOCALLY_TRAINED_PLAYERS = 39
    HOME_GROWN_PLAYERS = 146
    NATIONALS_UNDER_CONTRACT = 212


class SquadRuleBound(IntEnum):
    """Whether a rule's number is a floor or a ceiling.

    The two codes pair with the game's two phrasings of every squad rule: "Minimum of N ..."
    and "Maximum of N ...". On every pinned rule the minimum rules are the quotas a squad must
    meet and the maximum rules the limits it must stay under.
    """

    UNKNOWN = -1
    MINIMUM = 1
    MAXIMUM = 255


class SquadRuleMatchKind(StrEnum):
    """How a competition was matched to a rule set.

    COMPETITION: the set names the competition itself. DIVISION: the competition sits at a
    division level the set governs. GROUP: the competition is a group of such a level (a league
    split into regional groups, such as Türkiye's 2. League White and Red Groups); this link is
    a heuristic, see `SquadRuleSet.group_database_ids`.
    """

    COMPETITION = "competition"
    DIVISION = "division"
    GROUP = "group"


@dataclass(frozen=True, slots=True)
class SquadRule:
    """One squad registration rule of a rule set.

    Attributes:
        kind: The rule's code with its label, or UNKNOWN and the raw code.
        bound: Whether `value` is a minimum or a maximum, or None when the rule stores neither.
        value: The rule's number, or None for a rule that sets an amount of money instead.
        cash: The amount for a money rule (a salary cap, for one), in `currency`, or None.
        currency: The currency code of `cash`, or None.
        first_season: The first season the rule applies to, by its start year, or None when it
            applies from the start.
        last_season: The last season the rule applies to, by its start year, or None when it
            has no end.
        flags: The four-letter flags the rule carries, as the save names them, in stored order.
            Their meanings are not known.
        unknown: The rule's other numbers, keyed by the save's own four-letter tag.
    """

    UNKNOWN_KEYS: ClassVar[tuple[str, ...]] = ()

    kind: CodedValue[SquadRuleKind]
    bound: CodedValue[SquadRuleBound] | None
    value: int | None
    cash: int | None
    currency: int | None
    first_season: int | None
    last_season: int | None
    flags: tuple[str, ...]
    unknown: Mapping[str, int]

    def applies_in(self, season: int) -> bool:
        """Whether the rule applies in the season starting in `season`."""
        if self.first_season is not None and season < self.first_season:
            return False
        return self.last_season is None or season <= self.last_season


@dataclass(frozen=True, slots=True)
class SquadRuleSet:
    """One named set of squad registration rules from a nation's rules file.

    Attributes:
        name: The set's name as the database stores it (a label for editors, such as
            "PREMIER" or "ENG_FIXTURE_RULES_PREM"), or None when it has none.
        nation_id: The database id of the nation whose rules file holds the set (765 England,
            799 Türkiye), or None when its file stores the nation another way, and for the
            continental and international sets, which belong to no nation.
        highest_division_level: The highest division level the set governs, 0 being the top
            division, or None when the set governs no division by level.
        lowest_division_level: The lowest division level the set governs; 255 means every level
            from `highest_division_level` down. None when the set governs no division by level.
        competition_database_id: The editor database id of the competition the set names
            outright (England's Premier League set names 11), or None.
        division_database_ids: The editor database ids of the competitions at the division
            levels the set covers, top level first. Empty for a set that governs no division,
            such as most cup and youth sets.
        group_database_ids: The groups of the covered levels that are split into regional
            groups, such as Türkiye's 2. League White and Red Groups. **A heuristic**: the
            database marks a level as split into N groups and lists competitions in an order
            where the groups follow their level, and the N that follow are taken. That named
            the groups right for 13 of the 14 split levels checked by name (wrong for Wales),
            so this field is unconfirmed.
        rules: The set's rules, in stored order.
        unknown: The set's other numbers, keyed by the save's own four-letter tag.
    """

    UNKNOWN_KEYS: ClassVar[tuple[str, ...]] = ()

    name: str | None
    nation_id: int | None
    highest_division_level: int | None
    lowest_division_level: int | None
    competition_database_id: int | None
    division_database_ids: tuple[int, ...]
    group_database_ids: tuple[int, ...]
    rules: tuple[SquadRule, ...]
    unknown: Mapping[str, int]

    def rules_in(self, season: int) -> tuple[SquadRule, ...]:
        """The set's rules that apply in the season starting in `season`."""
        return tuple(rule for rule in self.rules if rule.applies_in(season))


@dataclass(frozen=True, slots=True)
class SquadRuleMatch:
    """A rule set that governs a competition, and how the competition was matched to it.

    Attributes:
        matched_by: Whether the set names the competition, covers its division level, or
            covers the level the competition is a group of.
        rule_set: The rule set.
    """

    matched_by: SquadRuleMatchKind
    rule_set: SquadRuleSet


register_field_statuses(
    SquadRule,
    verified=("value",),
    unconfirmed=("kind", "bound", "cash", "currency", "first_season", "last_season", "flags"),
)
register_field_statuses(
    SquadRuleSet,
    verified=(
        "name",
        "highest_division_level",
        "lowest_division_level",
        "competition_database_id",
        "division_database_ids",
    ),
    unconfirmed=("nation_id", "group_database_ids"),
)
