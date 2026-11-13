"""Tests for the FM24 save shapes: synthetic bytes laid out as the FM24 save of the
ground-truth career stores them (`Karl Hudgell - Unemployed.fm`, saved by build 1967899)."""

from __future__ import annotations

import struct
from datetime import date

from fmsave._layouts import FixtureCalendarLayout, GameInfoLayout, find_layout
from fmsave._layouts import fm24_24_3_0 as fm24
from fmsave.readers._common import SPAN_REGION
from fmsave.readers.career_history import decode_honour_rows
from fmsave.readers.manager_career import (
    FM24_RECORD_BYTES,
    TRAILER,
    decode_manager_career_records,
)
from fmsave.readers.transfer_history import decode_player_season_records

_UNSET = 0xFFFF_FFFF
_NULL_DATE = struct.pack("<HH", 1, 1900)


def packed_date(day: date) -> bytes:
    return struct.pack("<HH", day.timetuple().tm_yday, day.year)


def section_head(schema: int) -> bytes:
    return struct.pack("<BB4sHI", 3, 1, b"tad.", schema, 0)


def test_fm24_layouts_are_found_by_the_fm24_build() -> None:
    assert find_layout(GameInfoLayout, "game_info", 42, fm24.BUILD).layout is fm24.GAME_INFO
    fixtures = find_layout(FixtureCalendarLayout, SPAN_REGION, None, fm24.BUILD).layout
    assert fixtures is fm24.FIXTURE_CALENDAR
    assert (fixtures.record_bytes, fixtures.r39_42_bytes, fixtures.marker_byte_value) == (
        67,
        3,
        0x18,
    )


def test_fm24_transfer_rows_are_77_bytes_with_a_06_head() -> None:
    fm24_row = struct.Struct("<4sIBIBIBI8sIBHHHIIII19s")
    row = fm24_row.pack(
        bytes([0, 1, 1, 6]),
        720407,
        0,
        _UNSET,
        0,
        69,
        0,
        69,
        bytes(8),
        0,
        24,
        1,
        2037,
        257,
        2818,
        4,
        _UNSET,
        _UNSET,
        b"\xff" * 19,
    )
    data = (
        section_head(32) + row + row.replace(struct.pack("<I", 720407), struct.pack("<I", 720236))
    )
    records = decode_player_season_records(data)
    assert [r.player_reference for r in records] == [720407, 720236]
    first = records[0]
    assert (first.value_a, first.value_b, first.value_c, first.value_d) == (None, 69, 69, 0)
    assert (first.record_type, first.season_year, first.value_e, first.count) == (24, 2037, 2818, 4)


def test_fm26_heads_are_not_read_from_an_fm24_section() -> None:
    row = bytes([0, 1, 1, 7]) + bytes(73)
    assert decode_player_season_records(section_head(32) + row) == ()


def test_fm24_honour_rows_use_the_short_node() -> None:
    def honour(club: int, competition: int, flag: int, count: int, season: int) -> bytes:
        return (
            struct.pack("<IHI", club, 1, competition)
            + bytes([0x8B, 0x00, 0x02, flag])
            + struct.pack("<HBHHH", count, 4, 1, 1, season)
        )

    data = (
        bytes(16) + honour(5104183, 94029475, 0, 25, 2013) + honour(5100180, 30007231, 1, 20, 2019)
    )
    rows = decode_honour_rows(data)
    assert [(h.club_uid, h.competition_id, h.count, h.season) for h in rows] == [
        (5104183, 94029475, 25, 2013),
        (5100180, 30007231, 20, 2019),
    ]


def fm24_manager_record(reference: int, games: int, wins: int, losses: int) -> bytes:
    record = bytearray(FM24_RECORD_BYTES)
    struct.pack_into("<I", record, 0, reference)
    fee = (
        struct.pack("<I", 189608)
        + packed_date(date(2036, 8, 10))
        + struct.pack("<IHIBI", 22937564, 0, 706, 0, 603)
    )
    unset_fee = b"\xff" * 4 + _NULL_DATE + struct.pack("<IHIBI", _UNSET, 0, _UNSET, 0, _UNSET)
    record[4:27] = fee
    record[27:50] = unset_fee
    job = (
        b"\x00"
        + struct.pack("<I", 603)
        + packed_date(date(2023, 7, 18))
        + packed_date(date(2038, 3, 23))
    )
    unset_spell = b"\x00" + b"\xff" * 4 + _NULL_DATE * 2
    record[50:63] = job
    record[63:76] = job
    record[76:89] = unset_spell
    struct.pack_into("<I", record, 89, reference)
    struct.pack_into("<HHHHHII", record, 93, 1, 3, games, wins, losses, 1646, 1091)
    struct.pack_into("<HHHQQ", record, 111, 19, 132, 41, 116198649, 108360668)
    record[133] = 1
    struct.pack_into("<HHHHHII", record, 164, 1, 3, games, wins, losses, 1646, 1091)
    record[234:257] = unset_fee
    record[257:280] = fee
    record[293:306] = unset_spell
    record[306:319] = unset_spell
    struct.pack_into("<HHQ", record, 324, 50, 50, 2446234)
    return bytes(record)


def test_fm24_manager_records_are_fixed_358_byte_records() -> None:
    data = (
        section_head(27)
        + b"event logs"
        + fm24_manager_record(328408, 762, 405, 224)
        + fm24_manager_record(328409, 10, 5, 2)
        + TRAILER
    )
    first, second = decode_manager_career_records(data)
    assert (first.reference, first.games, first.wins, first.draws, first.losses) == (
        328408,
        762,
        405,
        133,
        224,
    )
    assert (first.cups, first.league_titles, first.goals_for, first.goals_against) == (
        1,
        3,
        1646,
        1091,
    )
    assert (first.money_spent, first.money_received, first.agent_fees) == (
        116198649,
        108360668,
        2446234,
    )
    assert (first.players_bought, first.players_sold, first.players_released) == (132, 41, 50)
    assert first.highest_fee_paid is not None and first.highest_fee_paid.fee == 22937564
    assert first.highest_fee_received is None
    assert first.longest_club_spell is not None and first.longest_club_spell.team_id == 603
    assert first.longest_national_spell is None
    assert first.club_jobs == 1
    assert first.current_job is not None
    assert (first.current_job.team_id, first.current_job.start_date) == (603, date(2023, 7, 18))
    assert first.current_job.highest_fee_paid == first.highest_fee_paid
    assert second.reference == 328409


def test_fm24_records_stop_where_the_reference_copy_does_not_match() -> None:
    broken = bytearray(fm24_manager_record(5, 1, 1, 0))
    struct.pack_into("<I", broken, 89, 6)
    data = section_head(27) + bytes(broken) + fm24_manager_record(7, 1, 1, 0) + TRAILER
    assert [r.reference for r in decode_manager_career_records(data)] == [7]
