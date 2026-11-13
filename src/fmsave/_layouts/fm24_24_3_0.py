"""Layouts for FM24 saves (database version 24.3.0+0, saved by build 1967899).

FM24 keeps no version string in `save_game_summary`; the game is read from `game_info`
instead (see `GameInfoVersionLayout`). Every history section carries the same schema number
as in FM26, so the history readers use the FM26 fallback layouts and check their results.
`game_db` (schema 3569) and other regions whose schemas differ from FM26's have no FM24
layouts yet, so the build stays unknown.
"""

from __future__ import annotations

import dataclasses

from fmsave._layouts import GameInfoLayout, GameInfoVersionLayout, LayoutEntry
from fmsave._layouts import fm26_26_3_2 as fm26

BUILD = "24.3.0+1967899"
# Builds every region's layout fits. None yet: `game_db` has no FM24 layouts.
KNOWN_BUILDS: tuple[str, ...] = ()

# The same fields as FM26's `game_info`, 20 bytes later from the build words on. Two build words
# follow the database version, at +54 and +58: the build the career was started on, then the
# build that saved the file. The in-game date sits at +199 and the late build word, ahead of the
# migration list, at +226 on the save measured.
GAME_INFO = GameInfoLayout(
    db_version_length_offset=8,
    max_db_version_bytes=64,
    build_number_offsets_after_db_version=(58,),
    game_date_offset_after_db_version=199,
    late_build_number_window_after_db_version=(203, 267),
)

GAME_INFO_VERSION = GameInfoVersionLayout(
    db_version_pattern=r"(24)\.([0-9]{1,3})\.([0-9]{1,4})\+0",
    db_version_length_offset=8,
    max_db_version_bytes=64,
    saved_by_build_offset_after_db_version=58,
)

# A club's team list opens with entries of 24 bytes led by 0x01, against FM26's 25 bytes led
# by 0x02; the rest of the list is FM26's. On the measured career every club's teams match
# the same career's FM26 save, apart from 7 clubs whose teams changed in play since.
TEAM_LISTS = dataclasses.replace(fm26.TEAM_LISTS, first_entry_bytes=24, first_entry_lead_byte=0x01)

# FM24 player records have one byte fewer than FM26's after the reputation bucket, so every
# field from the team id to the marker sits one byte earlier, and two more bytes ahead of the
# height, which sits one byte later (FM26 stores 02 where FM24 stores 01 4C 00). Measured on
# 219,376 players found in both saves of the same career: the header and abilities match,
# and the height matches on 209,082 (the rest are mostly young players who have grown since).
PLAYER_RECORDS = dataclasses.replace(
    fm26.PLAYER_RECORDS,
    marker_offset=101,
    team_id_offset=15,
    ratings_offset=23,
    attributes_offset=38,
    transfer_value_offset=92,
    club_join_date_offset=97,
    match_sharpness_offset=105,
    condition_offset=109,
    height_offset=122,
    decode_extent=123,
)

# A person block is FM26's with four bytes fewer after the birth date: FM24 stores one byte
# (FF) at +8 where FM26 stores five (05 00 02 00 02 on the person measured), so every field
# from the nation id on sits four bytes earlier. The name ids and legal name ahead of the
# birth date are where FM26 keeps them.
PERSON_BLOCKS = dataclasses.replace(
    fm26.PERSON_BLOCKS,
    personality_offset_from_birth=17,
    date_zero_bytes_offset_from_birth=10,
    nation_id_offset_from_birth=9,
    relation_present_offset_from_birth=33,
    relation_count_offset_from_birth=34,
    relation_entries_offset_from_birth=35,
)

# Fixture records are 67 bytes against FM26's 68: the unknown field at +39 is three bytes,
# not four (FM26's first byte, FF on the records compared, is missing), so the match rules
# template, the next unknown and the played byte sit one byte earlier. The record and result
# markers are 18, not 1C, and a result's header is FM26's but each of its counted items is
# 8 bytes, not 16 (FM26's eight FF bytes after the first u32 are missing).
FIXTURE_CALENDAR = dataclasses.replace(
    fm26.FIXTURE_CALENDAR,
    record_bytes=67,
    marker_byte_value=0x18,
    r39_42_bytes=3,
    match_rules_template_offset=42,
    r47_54_offset=46,
    played_offset=54,
    secondary_marker=0x18,
    secondary_item_bytes=8,
)

# Sponsor rows are 24 bytes against FM26's 25: the row tag is 01, not 02, and the zero byte
# FM26 keeps at +20 is missing, so the annual value sits at +20.
SPONSOR_CHAINS = dataclasses.replace(fm26.SPONSOR_CHAINS, row_bytes=24, tag=0x01, annual_offset=20)

# `tactics_man` carries FM26's schema number but FM24's own tags: header byte 0C, 3A where
# FM26 writes 42 (the selection's end marker, the record signature, the slot tag), three
# flag bytes in front of the set-piece marker's 05 02 (FM26 has one), a 01 in front of the
# order marker, and a byte of 01 after a tactic's name ahead of FM26's twelve zero bytes.
# Signatures end 00 on the manager's own tactics and 01 on presets; FM24 has no third kind.
# Names, style labels and mentality read on every team block of the measured save (mentality
# codes assumed FM26's: the same seven mentalities). FM24 slots are not FM26's in- and
# out-of-possession pairs, so no slot decodes and every tactic ships without slots.
TACTICS = dataclasses.replace(
    fm26.TACTICS,
    header_marker=0x0C,
    selection_end_marker=bytes.fromhex("3a00"),
    taker_markers=(
        bytes.fromhex("0000000502"),
        bytes.fromhex("0101000502"),
        bytes.fromhex("0501040502"),
    ),
    order_marker=bytes.fromhex("0108"),
    user_signature=bytes.fromhex("223a001802000000"),
    alternate_signature=bytes.fromhex("223a001802000000"),
    preset_signature=bytes.fromhex("223a001802000001"),
    name_zero_bytes=13,
    slot_tag=bytes.fromhex("3a0002"),
)

LAYOUTS: tuple[LayoutEntry, ...] = (
    LayoutEntry(region="game_info", schema=42, build=BUILD, layout=GAME_INFO),
    LayoutEntry(region="game_info", schema=42, build=BUILD, layout=GAME_INFO_VERSION),
    LayoutEntry(region="game_db", schema=3569, build=BUILD, layout=TEAM_LISTS),
    LayoutEntry(region="game_db", schema=3569, build=BUILD, layout=PLAYER_RECORDS),
    LayoutEntry(region="game_db", schema=3569, build=BUILD, layout=PERSON_BLOCKS),
    LayoutEntry(region="game_db", schema=3569, build=BUILD, layout=SPONSOR_CHAINS),
    LayoutEntry(region="tactics_man", schema=26, build=BUILD, layout=TACTICS),
    LayoutEntry(region=fm26.SPAN_REGION_NAME, schema=None, build=BUILD, layout=FIXTURE_CALENDAR),
)
