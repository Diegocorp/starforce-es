r"""Widths of runtime inserts: text the game puts into a message when it is shown.

What each insert is assumed to print (InsertResolver.describe() says which rule was used):
  _CMD_insert_option(0|1)      player given / family name: entries 0 / 1 of the base-only
                               rr1/message/rr1_player_name.msg.23 (English slot: 'Geo', 'Stelar').
  _CMD_insert_option(N >= 2)   other people's names and screen values: unknown, default width.
  _CMD_insert_itemname(id)     SF1 item / card name. The id is a game id, not an entry index: the English
  _CMD_insert_btlwarename(id)  script text/rr1/userdata/itemname.user.3.en (chipname.user.3.en for cards)
                               maps each id to a show-text command whose GUID names the msg entry.
                               Items (256 ids): 0-68 -> entries 0-68, 70-80 -> 69-79, 96-110 -> 80-94,
                               112-113 -> 95-96; 69, 81-95, 111 and 114+ are empty.
                               Cards (257 ids): 0-150 -> 0-150, 151-168 -> 206-223 (moved to the end of
                               the English table), 181-216 -> 151-186, 231-245 -> 187-201, 247 -> 202,
                               252-254 -> 203-205; 169-180, 217-230, 246, 248-251, 255+ are empty.
                               Ids with no entry use the widest entry of the table.
  _CMD_insert_itemnamefree(x)  runtime item / card variable: widest entry of the same table.
  _CMD_insert_btlwarenamefree(x)
  _CMD_insert_numdisp(fmt, v)  low nibble of fmt (hex) = digit count, measured with the widest digit;
                               low nibble 0 (no digit count; never used by SF1-SF3) gets the default.
  {n}                          3 digits.
  <REF EntryName>              text of that entry, found by name in any msg file of the paks
                               (index cached in work/cache/ref_index.json).
  <PLATMSG X>                  widest platform variant X_000, X_002, ... of common/localize.msg.23.
  <DATETIME {0} "fmt">         the format with every date/time field as digits and A as AM/PM;
                               "88:88" when no format is given.
  anything else                default: 8 average lowercase letters of the slot (metrics).
The rules above are those of SF1 (rr1). The game of the resolver comes from its scope (GAME_OF_SCOPE:
phase1 -> rr1, sf2 -> rr2, sf3 -> rr3; 'all' has no game and keeps the rr1 code path without name tables,
as before; sf3 rules below). The validator makes one resolver per base scope and measures
each work file with the resolver of its own scope; a build uses the resolver of its scope.

SF2 (rr2) rules. The meaning of each code was checked against the DS script commands it replaces
(research/ds/lc_align_sf2.jsonl + sf2_en.jsonl, named as in brackets) and the rr2 tables; the full table
with Spanish agreement notes is work/sf2_glossary/inserts/inserts_sf2.txt.
  Ids go through the English scripts text/rr2/userdata/<table>.user.3.en like the SF1 ones (game id ->
  show-text GUID -> entry); ids without an entry use the widest entry of the table(s).
  itemname(id) / itemnamefree(x)     rr2_itemname_eng (256 ids, 118 entries) [printItem / printItemBuffered]
  btlwarename(id)                    card id // 256 selects rr2_cardname / rr2_cardname2 / rr2_cardname3,
                                     card id % 256 is the script id (e.g. 2 -> cardname script 2 = entry 2,
                                     370 -> cardname2 script 114 = entry 50) [printCard]. 246 / 247 are
                                     the downloaded Mega / Giga Class cards (table entry '????', the real
                                     name comes with the download): widest card name.
  btlwarenamefree(x)                 widest name of the three card tables [printCardBuffered]
  attackstar()                       Star Card mark after a card name: widest of the rr2_cardname3 entries
                                     of script ids 251-253 (<REPLACE  RR2_STAR1..3>, one glyph)
                                     [printStarCardValue]
  abilityname(id)                    rr2_abilityname_eng name + "/" + 3 digits of Link Power cost
                                     [printAbility + "/" + printAbilityPoints]. Abilities come in up to 4
                                     cost variants with consecutive ids (rr2_abilityinfo has an entry for
                                     each) but only one id carries the name: an id without a name takes the
                                     name of the closest lower id of the same run (73 -> 70 = entry 28).
  abilityname_nocost(id)             the name only [printAbility]
  abilityname_free(x)                widest name + "/" + 3 digits [printAbilityBuffered + points]
  abilityname_nocost_free(x)         widest name [printAbilityBuffered]
  folder_name(id)                    rr2_deckname_eng, script id = id & 0x3F (65 -> script id 1) [printFolder]
  emnamefree(x)                      widest virus name of rr2_emname1_eng [printEnemyBuffered]
  subaru_name()                      'Geo' or 'Mega Man' (whichever applies at the time): the wider
                                     [printGeoMegaMan]
  numdisp(fmt, v), {n}, REF, PLATMSG, DATETIME   as for SF1 (low nibble of fmt = minLength of the DS
                                     printBuffer command)
  Player-entered texts: N average lowercase letters, N = the input field's maximum length (the asterisks
  of rr2_subinput_eng): handle 10, subject 12, message / secret 17 per line, Best Combo name 8.
  option(0..3)                       string buffer filled by the screen [printStringBuffer]: 0 = handle
                                     (10), 1 = mail subject or handle (12), 2 = mail message line or screen
                                     name (17), 3 = secret line (17); others default.
  brother(n), subplayer_plname(), subaru_profile_name(), autobrother_name()
                                     handle names (10) [printBrotherName, Brother pop-up placeholder,
                                     printPlayerName, printAutoBrotherName]
  legendcombo_name(id), bestcombo()  Best Combo name (8) [printLegendForce, printBestCombo]
  subaru_profile(n), brother_profile(n), wordbox_profile(n), brother_kotoba_free(n, ...)
                                     profile word n with the field's length SF2_PROFILE_LETTERS[n]
                                     [printProfilePlayer..., printProfileBrother..., printProfileBuffered...]
  brother_profile_name(n)            entry n of a random Brother's profile without colour: a profile word
                                     as above (5 = favorite celebrity), n = 10 the Brother's handle (10)
                                     [printProfileBrotherRandomName]
  option_profile(n)                  a profile word in a string buffer: the longest field (17)
                                     [printStringBufferColored]

SF3 (rr3) rules. SF3 has its own command names (none is used by any SF1 or SF2 text, in any language). The
meaning of each code was checked against its English and Japanese context and the rr3 tables; the full table
with Spanish agreement notes is work/sf3_glossary/inserts/inserts_sf3.txt. Ids go through the English
scripts text/rr3/userdata/<stem>.user.3.en (for every table used, game id = entry index); an id without an
entry uses the widest entry of the table. Tables are SF3_TABLES (keys = rr3 stems).
  areaname / cardname / emname / keyname (keyitemname) / keyword / mannname (manname) / nickname /
  popname (popupname) / subname (subcardname) / weaponname / fldrname (foldername) / pwrname (powerupname) /
  abliname (abilityname) (id)        that table's entry (SF3_ID_COMMANDS)
  insert_pl_name()                   the player: rr3_nickname 0 'Geo' or 1 'Mega Man', the wider
  ablity_lv(n)                       after abliname: "/" + 3 digits (Link Power cost of cost variant n)
  num_lN(d) / num_rN(d) / num_0rN(d) number slot N (left, right or zero padded): d digits (decimal or 0x)
  key_item_num()                     number of Omega Pieces: 2 digits
  sservername(), noisename_rN(), wout_area_name()   widest of rr3_sservername, rr3_noise_chg_name,
                                     rr3_areaname + rr3_g_areaname
  strN()                             string slot N filled by the screen (codename, card, item, ability, server,
                                     folder, team, noise name, Friend Code): the widest of a name of
                                     SF3_STRING_TABLES, a Codename (10 letters) and a Friend Code (12 digits)
  pro_text_NN(), pro_text_my_NN()    profile word NN of a Brother / of the player: input length of field NN
                                     (SF3_PROFILE_LETTERS, rr3_subinput_v_eng)
  pro_text_my_name(), pro_name_NN()  a Codename (10)
  pro_res_rN()                       answer N given to the Satella Police robot: 1 hometown (8), 2 sport (8)
  Codes SF3 does not use (option, itemname, ...) keep the old sf3 behaviour (default widths).
Selecting the rules by file: for_path(pak path) is the resolver of that file's game (scope_of_path: rr2 ->
sf2, rr3 -> sf3, rr1 / common -> phase1) with the same settings; for_scope(scope) likewise. A tool that
measures files of several games with one resolver gets the right rules by measuring each file with
resolver.for_path(path). Codes that only SF2 uses (SF2_ONLY_COMMANDS; no SF1 or SF3 text has them) are
handed to the sf2 resolver by every other resolver, and likewise SF3 codes (sf3_rule_name) to the sf3
resolver, so even a resolver of the wrong scope measures them as their game does; codes shared with SF1
(option, itemname, btlwarename, ...) follow the resolver's own scope.

Table texts are Spanish when the table's work file (work/strings/<scope>/<group>/<stem>.json) has the entry
with a status at or above `min_status` (draft < done < reviewed; default draft, pass the build's minimum)
and a non-empty es, otherwise English. Missing or unreadable work files are treated as untranslated.
InsertResolver.english() is the same resolver without work files (English texts only); limits that are
defined by the English (sfes.wrap observed limits) are measured with it. Text inside inserted entries is
measured with the same rules (codes inside card names, e.g. <REPLACE  RR1_EX>, count as usual); a
multi-line entry counts as its widest line.

CLI: run.py inserts [--scope phase1 | --path PAK_PATH] [--slot N] [--size PX] TEXT...
     e.g. "_CMD_insert_itemname(112)"; --path selects the scope of that file's game.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import codes, config, msg23
from . import scope as scopes
from .metrics import Metrics
from .pak import PakSet

TRANSLATED_ORDER = ("draft", "done", "reviewed")     # statuses whose es the build uses, lowest first
REF_INDEX_PATH = config.CACHE / "ref_index.json"
REF_SCHEMA = 1
LOCALIZE = "natives/stm/text/common/localize.msg.23"
PLACEHOLDER_DIGITS = 3
MAX_NESTING = 3
LINE_RX = re.compile(r"\r\n|\n|\r")
DATE_FIELD_RX = re.compile(r"Y+|M+|D+|h+|H+|m+|s+|A|a")


@dataclass(frozen=True)
class TableSpec:
    """A name table: its msg file and the English script that maps game ids to entries (None: id = index)."""
    msg: str
    script: str | None


SF1_TABLES = {
    "player": TableSpec("natives/stm/text/rr1/message/rr1_player_name.msg.23", None),
    "item": TableSpec("natives/stm/text/rr1/message/eng/rr1_itemname_eng.msg.23",
                      "natives/stm/text/rr1/userdata/itemname.user.3.en"),
    "card": TableSpec("natives/stm/text/rr1/message/eng/rr1_chipname_eng.msg.23",
                      "natives/stm/text/rr1/userdata/chipname.user.3.en"),
}
RR2_TEXT = "natives/stm/text/rr2/"


def _rr2_table(stem: str) -> TableSpec:
    return TableSpec(f"{RR2_TEXT}message/eng/rr2_{stem}_eng.msg.23", f"{RR2_TEXT}userdata/{stem}.user.3.en")


SF2_TABLES = {
    "item": _rr2_table("itemname"),
    "card": _rr2_table("cardname"),             # card ids 0-255
    "card2": _rr2_table("cardname2"),           # card ids 256-511
    "card3": _rr2_table("cardname3"),           # card ids 512-767; script ids 251-253 = star marks
    "ability": _rr2_table("abilityname"),
    "ability_info": _rr2_table("abilityinfo"),  # only its ids are used: which ids are abilities
    "folder": _rr2_table("deckname"),
    "enemy": _rr2_table("emname1"),             # viruses
}
RR3_TEXT = "natives/stm/text/rr3/"


def _rr3_table(stem: str) -> TableSpec:
    return TableSpec(f"{RR3_TEXT}message/eng/rr3_{stem}_eng.msg.23", f"{RR3_TEXT}userdata/{stem}.user.3.en")


# SF3 tables are keyed by their stem, so no SF1 key ('player', 'item', 'card') is ever found for sf3: the codes
# SF3 shares with no game keep the old sf3 behaviour (default widths).
SF3_TABLES = {stem: _rr3_table(stem) for stem in (
    "areaname", "g_areaname", "cardname", "spcardname", "subcardname", "keyitemname", "keyword", "abilityname",
    "powerupname", "emname", "manname", "nickname", "popupname", "sservername", "weaponname", "foldername",
    "noise_chg_name", "teamname")}
TABLES_BY_SCOPE = {"phase1": SF1_TABLES, "sf2": SF2_TABLES, "sf3": SF3_TABLES}
GAME_OF_SCOPE = {"phase1": "rr1", "sf2": "rr2", "sf3": "rr3"}
COMMON_TABLES = {"localize": TableSpec(LOCALIZE, None)}
# command name -> (table, lookup): 'id' = first argument is a game id, 'any' = runtime variable (widest)
TABLE_COMMANDS = {
    "insert_itemname": ("item", "id"),
    "insert_itemnamefree": ("item", "any"),
    "insert_btlwarename": ("card", "id"),
    "insert_btlwarenamefree": ("card", "any"),
}
PLAYER_OPTIONS = {0: "player given name", 1: "player family name"}

# ---- SF2 (rr2)
SF2_CARD_TABLES = ("card", "card2", "card3")    # card id // 256 -> table, card id % 256 -> script id
SF2_DOWNLOAD_CARDS = (246, 247)                 # downloaded Mega / Giga Class card: entry '????' -> widest
SF2_STAR_IDS = (251, 252, 253)                  # rr2_cardname3 script ids of <REPLACE  RR2_STAR1..3>
SF2_GEO_MEGAMAN = ("Geo", "Mega Man")           # printGeoMegaMan; both names stay the same in Spanish
SF2_ABILITY_VARIANTS = 4                        # cost variants per ability (consecutive ids)
SF2_ABILITY_COST = "/"                          # printAbility + "/" + printAbilityPoints
SF2_ABILITY_COST_DIGITS = 3                     # Link Power costs have up to 3 digits (numdisp 0x13)
SF2_FOLDER_ID_MASK = 0x3F                       # folder_name(65) = deckname script id 1
# Maximum lengths of player-entered texts: asterisks of the input fields of rr2_subinput_eng.
SF2_INPUT_LETTERS = {"handle": 10, "subject": 12, "message": 17, "secret": 17, "combo": 8}
# Profile words (index n of subaru_profile(n) etc.): field name and maximum length. The English input
# screen has no field for the catchphrase (rr2_subinput_eng 9 is blank); 3 is the Japanese limit.
SF2_PROFILE_FIELDS = ("favorite food", "hometown", "recent interests", "favorite TV show", "catchphrase",
                      "favorite celebrity", "dislikes", "words to live by", "treasured item",
                      "best physical feature")
SF2_PROFILE_LETTERS = (8, 8, 12, 10, 3, 10, 8, 17, 8, 8)
SF2_PROFILE_HANDLE = 10                         # brother_profile_name(10): the Brother's handle
# insert_option(n): the screen's string buffer n -> (most frequent content, letters).
SF2_OPTIONS = {0: ("handle name", 10), 1: ("mail subject or handle name", 12),
               2: ("mail message line or a name the screen shows", 17), 3: ("secret line", 17)}
# Codes that only SF2 uses (any other resolver hands them to the sf2 resolver).
SF2_ONLY_COMMANDS = frozenset({
    "insert_subaru_name", "insert_attackstar", "insert_subplayer_plname", "insert_subaru_profile",
    "insert_brother_profile", "insert_wordbox_profile", "insert_brother_kotoba_free",
    "insert_brother_profile_name", "insert_subaru_profile_name", "insert_autobrother_name",
    "insert_option_profile", "insert_bestcombo", "insert_folder_name", "insert_abilityname",
    "insert_abilityname_free", "insert_abilityname_nocost", "insert_abilityname_nocost_free",
})

# ---- SF3 (rr3)
# command (name without _CMD_) -> table whose entry the first argument names (game id through the script).
SF3_ID_COMMANDS = {
    "areaname": "areaname", "cardname": "cardname", "emname": "emname", "keyname": "keyitemname",
    "keyword": "keyword", "mannname": "manname", "nickname": "nickname", "popname": "popupname",
    "subname": "subcardname", "weaponname": "weaponname", "fldrname": "foldername", "pwrname": "powerupname",
    "abliname": "abilityname",
}
SF3_PLAYER_NICKNAMES = (0, 1)                   # rr3_nickname 0 'Geo', 1 'Mega Man': insert_pl_name()
SF3_WAVE_OUT_TABLES = ("areaname", "g_areaname")
# Everything a string slot _CMD_strN() holds in the English: names of these tables, a Codename, a Friend Code.
SF3_STRING_TABLES = ("cardname", "spcardname", "subcardname", "keyitemname", "abilityname", "powerupname",
                     "weaponname", "sservername", "foldername", "teamname", "noise_chg_name")
SF3_ABILITY_COST = "/"                          # ablity_lv(n): "/" + Link Power cost of cost variant n
SF3_ABILITY_COST_DIGITS = 3                     # "uses _CMD_num_r4(3)p of Link Power" (rr3_subability_w 3)
SF3_KEY_ITEM_DIGITS = 2                         # key_item_num(): the number of Omega Pieces
# Maximum lengths of player-entered texts: asterisks of the input fields of rr3_subinput_v_eng.
SF3_INPUT_LETTERS = {"codename": 10, "friend code": 12, "team name": 12, "message": 16, "folder name": 8}
# Profile words pro_text_NN / pro_text_my_NN: field NN (order of rr3_subprofileedit_v_eng 1-8) and the
# maximum length of its input field (rr3_subinput_v_eng 8-15).
SF3_PROFILE_FIELDS = ("favorite Battle Card", "favorite boss", "weakness", "hometown", "favorite sport",
                      "one-liner", "disliked food", "favorite game")
SF3_PROFILE_LETTERS = (10, 11, 11, 8, 8, 16, 8, 15)
# pro_res_rN: answer N the player gives the Satella Police robot (rr3_npc_st9002: 1 hometown, 2 sport).
SF3_PROFILE_ANSWERS = {1: 3, 2: 4}              # answer slot -> profile field
SF3_PATTERNS = (                                # name pattern -> rule name (see InsertResolver._sf3_*)
    (re.compile(r"str\d+"), "string"),
    (re.compile(r"num_(?:l|r|0r)\d+"), "number"),
    (re.compile(r"pro_text_(?:my_)?(\d+)"), "profile"),
    (re.compile(r"pro_text_my_name|pro_name_\d+"), "codename"),
    (re.compile(r"pro_res_r(\d+)"), "answer"),
    (re.compile(r"noisename_r\d+"), "noise"),
)
SF3_FIXED_COMMANDS = {"insert_pl_name": "player", "ablity_lv": "cost", "key_item_num": "pieces",
                      "sservername": "server", "wout_area_name": "wave_out"}


def sf3_rule_name(name: str) -> str | None:
    """Rule of an SF3 command name ('table' for SF3_ID_COMMANDS), or None when SF3 has no rule for it."""
    if name in SF3_ID_COMMANDS:
        return "table"
    if name in SF3_FIXED_COMMANDS:
        return SF3_FIXED_COMMANDS[name]
    return next((rule for rx, rule in SF3_PATTERNS if rx.fullmatch(name)), None)


def scope_of_path(path: str) -> str:
    """Base scope of a msg path's game: rr2 -> sf2, rr3 -> sf3, anything else (rr1, common) -> phase1."""
    for scope, game in GAME_OF_SCOPE.items():
        if path.startswith(f"natives/stm/text/{game}/"):
            return scope
    return "phase1"


@dataclass(frozen=True)
class Assumption:
    """What an insert prints: the widest of `texts`, plus `digits` digits, plus `letters` average lowercase
    letters, or the default width. `rule` is one of player, table_id, table_widest, digits, ref, platmsg,
    datetime, default (SF1 and SF2), input (player-entered text), names (fixed names; SF2), slot (SF3 string
    slot: the widest of `alternatives`); `note` says what was assumed (for reports)."""
    rule: str
    note: str
    texts: tuple[str, ...] = ()
    digits: int = 0
    default: bool = False
    letters: int = 0
    alternatives: tuple["Assumption", ...] = ()    # when given: the width is the widest of these


@dataclass
class Table:
    """Entries of a name table in file order, each with its chosen text and language ('es' or 'en')."""
    names: list[str]
    texts: list[str]
    langs: list[str]
    by_id: list[int | None] | None = None   # game id -> entry index (None: no entry); None: id = index

    def index_of(self, game_id: int) -> int | None:
        if self.by_id is None:
            return game_id if 0 <= game_id < len(self.names) else None
        return self.by_id[game_id] if 0 <= game_id < len(self.by_id) else None


# ---------------------------------------------------------------------------- English message scripts

_T_CMD, _T_BLOCK, _T_ROOT = 0x7F7EFE9C, 0x5BB3ECA0, 0xA94249FE
_SHOW_TEXT = 0xAC


def _align(pos: int, n: int) -> int:
    return pos + (-pos) % n


def _read_command(d: bytes, p: int) -> tuple[tuple[int, str], int]:
    p = _align(p, 4)
    op = struct.unpack_from("<I", d, p)[0]
    p = _align(p + 4, 8)
    guid = str(uuid.UUID(bytes_le=bytes(d[p:p + 16])))
    p += 16
    (n,) = struct.unpack_from("<I", d, p)
    p += 4
    for _ in range(n):
        p = _align(p, 4)
        (ln,) = struct.unpack_from("<I", d, p)
        p += 4 + 2 * ln
    return (op, guid), p


def script_guids(raw: bytes) -> list[str | None]:
    """Per message id of an English text script (*.user.3.en): GUID of the entry it shows, or None.
    Layout (Phase 1, tools/investigate/other_script_decode.py): USR header -> RSZ block whose instances are
    commands (u32 op, Guid, string[] params), blocks (2 x u32) and one root (cmd refs, entry points per id,
    block refs). Message id k runs from entry_points[k] to entry_points[k+1] in command order."""
    if raw[:4] != b"USR\0":
        raise ValueError("not a USR file")
    (base,) = struct.unpack_from("<Q", raw, 32)
    if raw[base:base + 4] != b"RSZ\0":
        raise ValueError("no RSZ block")
    (count,) = struct.unpack_from("<I", raw, base + 12)
    inst_off, data_off = struct.unpack_from("<2Q", raw, base + 24)
    types = [struct.unpack_from("<I", raw, base + inst_off + 8 * i)[0] for i in range(count)]
    p = base + data_off
    commands: dict[int, tuple[int, str]] = {}
    root: list[list[int]] = []
    for i, t in enumerate(types[1:], 1):
        if t == _T_CMD:
            commands[i], p = _read_command(raw, p)
        elif t == _T_BLOCK:
            p = _align(p, 4) + 8
        elif t == _T_ROOT:
            p = _align(p, 4)
            for _ in range(3):
                (n,) = struct.unpack_from("<I", raw, p)
                root.append(list(struct.unpack_from(f"<{n}I", raw, p + 4)))
                p += 4 + 4 * n
        else:
            raise ValueError(f"unknown instance type {t:08x}")
    if len(root) != 3:
        raise ValueError("script has no root object")
    ordered = [commands[r] for r in root[0]]
    starts = root[1] + [len(ordered)]
    return [next((g for op, g in ordered[a:b] if op == _SHOW_TEXT), None) for a, b in zip(starts, starts[1:])]


# -------------------------------------------------------------------------------------- work files

def _read_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def statuses_from(min_status: str) -> frozenset[str]:
    """Statuses whose Spanish is used: `min_status` and every higher one of TRANSLATED_ORDER."""
    if min_status not in TRANSLATED_ORDER:
        raise ValueError(f"min_status must be one of {TRANSLATED_ORDER}, not {min_status!r}")
    return frozenset(TRANSLATED_ORDER[TRANSLATED_ORDER.index(min_status):])


def spanish_texts(doc: dict, statuses: frozenset[str]) -> dict[str, str]:
    """entry name -> es for translated entries (status in `statuses`, es not empty)."""
    out = {}
    for e in doc.get("entries", []):
        if isinstance(e, dict) and e.get("status") in statuses and e.get("es"):
            out[e.get("name")] = e["es"]
    return out


class WorkTexts:
    """Spanish texts per msg path, read lazily from work/strings/<scope>/<group>/<stem>.json.
    `work_dir` None means no work files (every text is English)."""

    def __init__(self, work_dir: Path | None, scope: str, statuses: frozenset[str]):
        self.work_dir = work_dir
        self.scope = scope
        self.statuses = statuses
        self._files: dict[str, Path] | None = None
        self._texts: dict[str, dict[str, str]] = {}

    def _index(self) -> dict[str, Path]:
        if self._files is None:
            found = self.work_dir.glob("*/*/*.json") if self.work_dir else ()
            self._files = {}
            for p in sorted(found, key=lambda p: (p.parts[-3] != self.scope, str(p))):   # own scope first
                self._files.setdefault(p.stem, p)
        return self._files

    def for_path(self, msg_path: str) -> dict[str, str]:
        if msg_path not in self._texts:
            wp = self._index().get(scopes.stem_of(msg_path))
            self._texts[msg_path] = spanish_texts(_read_json(wp), self.statuses) if wp else {}
        return self._texts[msg_path]


# ------------------------------------------------------------------------------------- REF index

def _paks_identity(ps: PakSet) -> list[list]:
    out = []
    for pak in ps.paks:
        st = os.stat(pak.path)
        out.append([pak.path.name, st.st_size, st.st_mtime_ns])
    return out


def build_ref_index(ps: PakSet) -> dict[str, dict[str, str]]:
    """name -> {"path", "en"} for every entry that some English text references with <REF name>."""
    entries: dict[str, tuple[str, str]] = {}
    wanted: set[str] = set()
    for path in sorted(scopes.msg_paths(ps)):
        m = msg23.parse(ps.read(path))
        for e in m.entries:
            en = m.get(e, config.LANG_EN)
            entries[e.name] = (path, en)
            wanted.update(t.args.strip() for t in codes.tokenize(en) if t.kind == "tag" and t.name == "REF")
    return {n: {"path": entries[n][0], "en": entries[n][1]} for n in sorted(wanted) if n in entries}


def load_ref_index(ps: PakSet, cache_path: Path = REF_INDEX_PATH) -> dict[str, dict[str, str]]:
    """REF targets from the cache, rebuilt when the official paks change (name, size, mtime)."""
    ident = _paks_identity(ps)
    doc = _read_json(cache_path)
    if doc.get("schema") == REF_SCHEMA and doc.get("paks") == ident:
        return doc["targets"]
    targets = build_ref_index(ps)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.tmp")
    body = {"schema": REF_SCHEMA, "paks": ident, "targets": targets}
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, cache_path)
    return targets


# ------------------------------------------------------------------------------------------ helpers

def _first_arg(token: codes.Token) -> str:
    return (token.args or "").split(",")[0].strip()


def _int_arg(s: str) -> int | None:
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s, 10)
    except ValueError:
        return None


def _hex_arg(s: str) -> int | None:
    try:
        return int(s[2:] if s.lower().startswith("0x") else s, 16)
    except ValueError:
        return None


def datetime_assumption(args: str) -> Assumption:
    """<DATETIME {0} "hh:mm A MM/DD/YYYY">: digit fields as digits, A as the wider of AM/PM."""
    m = re.search(r'"([^"]*)"', args)
    if m is None:
        return Assumption("datetime", 'no format: "88:88"', texts=(":",), digits=4)
    fmt = m.group(1)
    fields = DATE_FIELD_RX.findall(fmt)
    digits = sum(len(f) for f in fields if f not in ("A", "a"))
    literal = tuple(dict.fromkeys(_date_literals(fmt, ampm) for ampm in ("AM", "PM")))
    ampm = ", A as AM/PM" if len(literal) > 1 else ""
    return Assumption("datetime", f'format "{fmt}": {digits} digits{ampm}', texts=literal, digits=digits)


def _date_literals(fmt: str, ampm: str) -> str:
    """The non-digit part of a date format: literals kept, digit fields dropped, A/a replaced by `ampm`."""
    return DATE_FIELD_RX.sub(lambda f: ampm if f.group() in ("A", "a") else "", fmt)


def _quote(text: str, limit: int = 40) -> str:
    s = text.replace("\r\n", "/")
    return repr(s if len(s) <= limit else s[:limit - 3] + "...")


def _input(what: str, letters: int) -> Assumption:
    """A player-entered text of at most `letters` characters, measured as that many average lowercase letters."""
    return Assumption("input", f"{what}: {letters} average lowercase letters", letters=letters)


# ---------------------------------------------------------------------------------------- resolver

class InsertResolver:
    """Width in em of runtime inserts, per font slot. Pass it to Metrics.line_em / lines_px / wrap.
    `work_dir` defaults to work/strings; `english_only` ignores the work files (see english())."""

    def __init__(self, metrics: Metrics, scope: str = "phase1", work_dir: Path | None = None,
                 paks: PakSet | None = None, ref_index_path: Path = REF_INDEX_PATH, *,
                 min_status: str = TRANSLATED_ORDER[0], english_only: bool = False):
        self.metrics = metrics
        self.scope = scope
        self.game = GAME_OF_SCOPE.get(scope)
        self.english_only = english_only
        self.min_status = min_status
        work = None if english_only else Path(work_dir) if work_dir else config.STRINGS
        self.work = WorkTexts(work, scope, statuses_from(min_status))
        self._paks = paks
        self._ref_index_path = Path(ref_index_path)
        self._refs: dict[str, dict[str, str]] | None = None
        self._tables: dict[str, Table | None] = {}
        self._assumptions: dict[str, Assumption] = {}
        self._widths: dict[tuple[str, int], float] = {}
        self._depth = 0
        self._english: InsertResolver | None = None
        self._others: dict[str, InsertResolver] = {}

    @property
    def paks(self) -> PakSet:
        if self._paks is None:
            self._paks = PakSet()
        return self._paks

    def english(self) -> InsertResolver:
        """This resolver without work files: every table and REF text is the English one."""
        if self.english_only:
            return self
        if self._english is None:
            self._english = InsertResolver(self.metrics, self.scope, paks=self.paks,
                                           ref_index_path=self._ref_index_path, english_only=True)
        return self._english

    def for_scope(self, scope: str) -> InsertResolver:
        """A resolver of `scope` with this resolver's settings (work files, paks, min_status, english_only);
        itself for its own scope. Created once per scope."""
        if scope == self.scope:
            return self
        if scope not in self._others:
            other = InsertResolver(self.metrics, scope, self.work.work_dir, self.paks, self._ref_index_path,
                                   min_status=self.min_status, english_only=self.english_only)
            other._others[self.scope] = self        # and back: one resolver per scope in the family
            self._others[scope] = other
        return self._others[scope]

    def for_path(self, path: str) -> InsertResolver:
        """The resolver whose rules apply to the msg file `path`: for_scope(scope_of_path(path))."""
        return self.for_scope(scope_of_path(path))

    def _delegate(self, token: codes.Token) -> InsertResolver | None:
        """The sf2 resolver for a code that only SF2 uses, the sf3 resolver for a code that only SF3 uses
        (sf3_rule_name; no SF1 or SF2 text has them), when this is not that resolver; else None."""
        if token.kind != "cmd":
            return None
        if self.scope != "sf2" and token.name in SF2_ONLY_COMMANDS:
            return self.for_scope("sf2")
        if self.scope != "sf3" and sf3_rule_name(token.name or "") is not None:
            return self.for_scope("sf3")
        return None

    # ---- public API

    def width_em(self, token: codes.Token, slot: int) -> float:
        other = self._delegate(token)
        if other is not None:
            return other.width_em(token, slot)
        key = (token.raw, slot)
        if key in self._widths:
            return self._widths[key]
        width = self._measure(self.assumption(token), slot)
        if self._depth == 0:
            self._widths[key] = width
        return width

    def describe(self, token: codes.Token) -> str:
        return f"{token.raw}: {self.assumption(token).note}"

    def assumption(self, token: codes.Token) -> Assumption:
        other = self._delegate(token)
        if other is not None:
            return other.assumption(token)
        if token.raw not in self._assumptions:
            self._assumptions[token.raw] = self._assume(token)
        return self._assumptions[token.raw]

    def table(self, name: str) -> Table | None:
        """A name table of the scope (SF1_TABLES / SF2_TABLES keys) or 'localize'; None if unknown or missing."""
        if name not in self._tables:
            spec = TABLES_BY_SCOPE.get(self.scope, {}).get(name) or COMMON_TABLES.get(name)
            self._tables[name] = self._load_table(spec) if spec else None
        return self._tables[name]

    # ---- measuring

    def _measure(self, a: Assumption, slot: int) -> float:
        if a.default:
            return self.metrics.default_insert_em(slot)
        if a.alternatives:
            return max(self._measure(x, slot) for x in a.alternatives)
        text = max((self._text_em(t, slot) for t in a.texts), default=0.0)
        width = text + a.digits * self.metrics.digit_em(slot)
        if a.letters:
            width += a.letters * self.metrics.avg_lowercase_em(slot)
        return width

    def _text_em(self, text: str, slot: int) -> float:
        nested = self if self._depth < MAX_NESTING else None
        self._depth += 1
        try:
            return max(self.metrics.line_em(line, slot, nested) for line in LINE_RX.split(text))
        finally:
            self._depth -= 1

    # ---- rules

    def _assume(self, token: codes.Token) -> Assumption:
        if token.kind == "ph":
            return Assumption("digits", f"placeholder: {PLACEHOLDER_DIGITS} digits",
                              digits=PLACEHOLDER_DIGITS)
        if token.kind == "tag":
            return self._assume_tag(token)
        if token.name == "insert_numdisp":
            return self._assume_numdisp(token)
        if self.game == "rr2":
            return self._assume_sf2(token)
        if self.game == "rr3":
            found = self._assume_sf3(token)
            if found is not None:
                return found
        if token.name == "insert_option":
            return self._assume_option(token)
        if token.name in TABLE_COMMANDS:
            return self._assume_table(token, *TABLE_COMMANDS[token.name])
        return self._default("no rule for this insert")

    def _assume_numdisp(self, token: codes.Token) -> Assumption:
        fmt = _hex_arg(_first_arg(token))
        digits = 0 if fmt is None else fmt & 0xF
        if digits:
            return Assumption("digits", f"number: {digits} digits", digits=digits)
        return self._default(f"number format {_first_arg(token)!r} has no digit count")

    def _default(self, why: str) -> Assumption:
        return Assumption("default", f"{why}; default 8 x average lowercase", default=True)

    def _assume_tag(self, token: codes.Token) -> Assumption:
        target = (token.args or "").strip()
        if token.name == "REF":
            return self._assume_ref(target)
        if token.name == "PLATMSG":
            return self._assume_platmsg(target)
        if token.name == "DATETIME":
            return datetime_assumption(token.args or "")
        return self._default(f"unknown text tag {token.name}")

    def _assume_option(self, token: codes.Token) -> Assumption:
        n = _int_arg(_first_arg(token))
        table = self.table("player")
        if n not in PLAYER_OPTIONS or table is None or n >= len(table.names):
            return self._default(f"option({_first_arg(token)}) is filled at run time")
        note = f"{PLAYER_OPTIONS[n]} {table.names[n]} = {_quote(table.texts[n])} ({table.langs[n]})"
        return Assumption("player", note, texts=(table.texts[n],))

    def _assume_table(self, token: codes.Token, name: str, lookup: str) -> Assumption:
        table = self.table(name)
        if table is None:
            return self._default(f"no {name} table for scope {self.scope}")
        widest = Assumption("table_widest", f"widest of {len(table.names)} {name} names",
                            texts=tuple(table.texts))
        if lookup == "any":
            return widest
        game_id = _int_arg(_first_arg(token))
        idx = None if game_id is None else table.index_of(game_id)
        if idx is None:
            return Assumption("table_widest", f"{name} id {_first_arg(token)} has no entry; " + widest.note,
                              texts=widest.texts)
        note = f"{name} id {game_id} -> {table.names[idx]} = {_quote(table.texts[idx])} ({table.langs[idx]})"
        return Assumption("table_id", note, texts=(table.texts[idx],))

    def _assume_ref(self, target: str) -> Assumption:
        if self._refs is None:
            self._refs = load_ref_index(self.paks, self._ref_index_path)
        ref = self._refs.get(target)
        if ref is None:
            return self._default(f"REF target {target} not found")
        es = self.work.for_path(ref["path"]).get(target)
        text, lang = (es, "es") if es else (ref["en"], "en")
        return Assumption("ref", f"REF {target} = {_quote(text)} ({lang})", texts=(text,))

    def _assume_platmsg(self, target: str) -> Assumption:
        table = self.table("localize")
        pairs = zip(table.names, table.texts) if table else ()
        variants = [t for n, t in pairs if n.startswith(target + "_")]
        if not variants:
            return self._default(f"no platform variants for {target}")
        return Assumption("platmsg", f"widest of {len(variants)} variants of {target}", texts=tuple(variants))

    # ---- SF2 rules

    def _assume_sf2(self, token: codes.Token) -> Assumption:
        rule = SF2_RULES.get(token.name or "")
        return rule(self, token) if rule else self._default("no SF2 rule for this insert")

    def _sf2_option(self, token: codes.Token) -> Assumption:
        n = _int_arg(_first_arg(token))
        if n not in SF2_OPTIONS:
            return self._default(f"option({_first_arg(token)}) is filled at run time")
        what, letters = SF2_OPTIONS[n]
        return _input(f"string buffer {n}: {what}", letters)

    def _sf2_item(self, token: codes.Token) -> Assumption:
        return self._assume_table(token, "item", "id")

    def _sf2_item_free(self, token: codes.Token) -> Assumption:
        return self._assume_table(token, "item", "any")

    def _sf2_enemy_free(self, token: codes.Token) -> Assumption:
        return self._assume_table(token, "enemy", "any")

    def _sf2_cards_widest(self, why: str = "") -> Assumption:
        tables = [t for t in (self.table(n) for n in SF2_CARD_TABLES) if t is not None]
        if not tables:
            return self._default(f"no card tables for scope {self.scope}")
        texts = tuple(text for t in tables for text in t.texts)
        return Assumption("table_widest", f"{why}widest of {len(texts)} card names (3 tables)", texts=texts)

    def _sf2_card(self, token: codes.Token) -> Assumption:
        game_id = _int_arg(_first_arg(token))
        if game_id is None or game_id < 0:
            return self._sf2_cards_widest(f"card id {_first_arg(token)} has no entry; ")
        if game_id in SF2_DOWNLOAD_CARDS:
            return self._sf2_cards_widest(f"card id {game_id} is a downloaded card (name not in the tables); ")
        k, script_id = divmod(game_id, 256)
        name = SF2_CARD_TABLES[k] if k < len(SF2_CARD_TABLES) else None
        table = self.table(name) if name else None
        idx = table.index_of(script_id) if table else None
        if idx is None:
            return self._sf2_cards_widest(f"card id {game_id} has no entry; ")
        note = (f"card id {game_id} -> {name} {script_id} -> {table.names[idx]} = {_quote(table.texts[idx])} "
                f"({table.langs[idx]})")
        return Assumption("table_id", note, texts=(table.texts[idx],))

    def _sf2_card_free(self, token: codes.Token) -> Assumption:
        return self._sf2_cards_widest()

    def _sf2_star(self, token: codes.Token) -> Assumption:
        table = self.table("card3")
        idx = [i for i in (table.index_of(s) for s in SF2_STAR_IDS) if i is not None] if table else []
        if not idx:
            return self._default("no star marks in rr2_cardname3")
        return Assumption("table_widest", f"Star Card mark: widest of {len(idx)} star glyph entries "
                                          f"(nothing for other cards)", texts=tuple(table.texts[i] for i in idx))

    def _sf2_geo(self, token: codes.Token) -> Assumption:
        return Assumption("names", "'Geo' or 'Mega Man' (the wider)", texts=SF2_GEO_MEGAMAN)

    def ability_index(self, game_id: int) -> int | None:
        """rr2_abilityname entry of an ability id: its own name, or the name of the closest lower id of the
        same run of cost variants (ids that have an rr2_abilityinfo entry, at most 3 steps down)."""
        names, info = self.table("ability"), self.table("ability_info")
        if names is None:
            return None
        for k in range(game_id, max(game_id - SF2_ABILITY_VARIANTS, -1), -1):
            idx = names.index_of(k)
            if idx is not None:
                return idx
            if info is None or info.index_of(k) is None:
                return None
        return None

    def _sf2_ability(self, token: codes.Token, cost: bool, free: bool) -> Assumption:
        table = self.table("ability")
        if table is None:
            return self._default(f"no ability table for scope {self.scope}")
        suffix = SF2_ABILITY_COST if cost else ""
        digits = SF2_ABILITY_COST_DIGITS if cost else 0
        tail = f" + '{suffix}' + {digits} digits of cost" if cost else ""
        game_id = None if free else _int_arg(_first_arg(token))
        idx = None if game_id is None else self.ability_index(game_id)
        if idx is None:
            why = "" if free else f"ability id {_first_arg(token)} has no entry; "
            return Assumption("table_widest", f"{why}widest of {len(table.names)} ability names{tail}",
                              texts=tuple(t + suffix for t in table.texts), digits=digits)
        note = (f"ability id {game_id} -> {table.names[idx]} = {_quote(table.texts[idx])} "
                f"({table.langs[idx]}){tail}")
        return Assumption("table_id", note, texts=(table.texts[idx] + suffix,), digits=digits)

    def _sf2_folder(self, token: codes.Token) -> Assumption:
        game_id = _int_arg(_first_arg(token))
        table = self.table("folder")
        if table is None:
            return self._default(f"no folder table for scope {self.scope}")
        idx = None if game_id is None else table.index_of(game_id & SF2_FOLDER_ID_MASK)
        if idx is None:
            return Assumption("table_widest", f"folder id {_first_arg(token)} has no entry; widest of "
                                              f"{len(table.names)} folder names", texts=tuple(table.texts))
        note = f"folder id {game_id} -> {table.names[idx]} = {_quote(table.texts[idx])} ({table.langs[idx]})"
        return Assumption("table_id", note, texts=(table.texts[idx],))

    def _sf2_handle(self, token: codes.Token) -> Assumption:
        return _input("handle name", SF2_INPUT_LETTERS["handle"])

    def _sf2_combo(self, token: codes.Token) -> Assumption:
        return _input("Best Combo name", SF2_INPUT_LETTERS["combo"])

    def _sf2_profile(self, token: codes.Token) -> Assumption:
        n = _int_arg(_first_arg(token))
        if n is None or not 0 <= n < len(SF2_PROFILE_LETTERS):
            return _input(f"profile word {_first_arg(token)} (unknown field): the longest field",
                          max(SF2_PROFILE_LETTERS))
        return _input(f"profile word {n} ({SF2_PROFILE_FIELDS[n]})", SF2_PROFILE_LETTERS[n])

    def _sf2_profile_name(self, token: codes.Token) -> Assumption:
        if _int_arg(_first_arg(token)) == SF2_PROFILE_HANDLE:
            return _input(f"profile entry {SF2_PROFILE_HANDLE}: Brother's handle name", SF2_INPUT_LETTERS["handle"])
        return self._sf2_profile(token)

    def _sf2_profile_widest(self, token: codes.Token) -> Assumption:
        return _input("a profile word: the longest field", max(SF2_PROFILE_LETTERS))

    # ---- SF3 rules

    def _assume_sf3(self, token: codes.Token) -> Assumption | None:
        """The SF3 rule of a command, or None (no SF3 rule: the caller keeps the old rr3 behaviour)."""
        if token.kind != "cmd":
            return None
        rule = sf3_rule_name(token.name or "")
        return getattr(self, f"_sf3_{rule}")(token) if rule else None

    def _sf3_widest(self, names: tuple[str, ...], what: str, why: str = "") -> Assumption:
        tables = [(n, t) for n, t in ((n, self.table(n)) for n in names) if t is not None]
        if not tables:
            return self._default(f"no {what} table for scope {self.scope}")
        texts = tuple(text for _, t in tables for text in t.texts)
        return Assumption("table_widest", f"{why}widest of {len(texts)} {what} (rr3_{'/'.join(n for n, _ in tables)})",
                          texts=texts)

    def _sf3_table(self, token: codes.Token) -> Assumption:
        name = SF3_ID_COMMANDS[token.name]
        table = self.table(name)
        game_id = _int_arg(_first_arg(token))
        idx = None if table is None or game_id is None else table.index_of(game_id)
        if idx is None:
            return self._sf3_widest((name,), "names", f"{name} id {_first_arg(token)} has no entry; ")
        note = f"{name} id {game_id} -> {table.names[idx]} = {_quote(table.texts[idx])} ({table.langs[idx]})"
        return Assumption("table_id", note, texts=(table.texts[idx],))

    def _sf3_player(self, token: codes.Token) -> Assumption:
        table = self.table("nickname")
        if table is None or any(table.index_of(k) is None for k in SF3_PLAYER_NICKNAMES):
            return Assumption("names", "'Geo' or 'Mega Man' (the wider; no rr3_nickname)", texts=SF2_GEO_MEGAMAN)
        idx = [table.index_of(k) for k in SF3_PLAYER_NICKNAMES]
        shown = " or ".join(f"{_quote(table.texts[i])} ({table.langs[i]})" for i in idx)
        return Assumption("names", f"player name: nickname {shown}, the wider",
                          texts=tuple(table.texts[i] for i in idx))

    def _sf3_cost(self, token: codes.Token) -> Assumption:
        return Assumption("digits", f"ability cost variant {_first_arg(token)}: '{SF3_ABILITY_COST}' + "
                                    f"{SF3_ABILITY_COST_DIGITS} digits of Link Power",
                          texts=(SF3_ABILITY_COST,), digits=SF3_ABILITY_COST_DIGITS)

    def _sf3_pieces(self, token: codes.Token) -> Assumption:
        return Assumption("digits", f"number of Omega Pieces: {SF3_KEY_ITEM_DIGITS} digits",
                          digits=SF3_KEY_ITEM_DIGITS)

    def _sf3_number(self, token: codes.Token) -> Assumption:
        digits = _int_arg(_first_arg(token))
        if not digits or digits < 0:
            return self._default(f"number {token.name}({_first_arg(token)}) has no digit count")
        return Assumption("digits", f"number slot {token.name}: {digits} digits", digits=digits)

    def _sf3_server(self, token: codes.Token) -> Assumption:
        return self._sf3_widest(("sservername",), "Satellite Server names")

    def _sf3_noise(self, token: codes.Token) -> Assumption:
        return self._sf3_widest(("noise_chg_name",), "Noise names")

    def _sf3_wave_out(self, token: codes.Token) -> Assumption:
        return self._sf3_widest(SF3_WAVE_OUT_TABLES, "area names")

    def _sf3_codename(self, token: codes.Token) -> Assumption:
        return _input("Codename", SF3_INPUT_LETTERS["codename"])

    def _sf3_profile_field(self, n: int | None, what: str) -> Assumption:
        if n is None or not 0 <= n < len(SF3_PROFILE_LETTERS):
            return _input(f"{what} (unknown field): the longest field", max(SF3_PROFILE_LETTERS))
        return _input(f"{what} {n} ({SF3_PROFILE_FIELDS[n]})", SF3_PROFILE_LETTERS[n])

    def _sf3_profile(self, token: codes.Token) -> Assumption:
        m = SF3_PATTERNS[2][0].fullmatch(token.name)
        mine = "_my_" in token.name
        return self._sf3_profile_field(int(m.group(1), 10), "own profile word" if mine else "profile word")

    def _sf3_answer(self, token: codes.Token) -> Assumption:
        n = int(SF3_PATTERNS[4][0].fullmatch(token.name).group(1), 10)
        return self._sf3_profile_field(SF3_PROFILE_ANSWERS.get(n), f"answer {n}: profile word")

    def _sf3_string(self, token: codes.Token) -> Assumption:
        names = self._sf3_widest(SF3_STRING_TABLES, "names")
        alternatives = (names, _input("Codename", SF3_INPUT_LETTERS["codename"]),
                        Assumption("digits", "Friend Code: 12 digits", digits=SF3_INPUT_LETTERS["friend code"]))
        if names.default:
            alternatives = alternatives[1:]
        return Assumption("slot", f"string slot {token.name}: the widest of a name (" + ", ".join(
            SF3_STRING_TABLES) + "), a Codename (10 letters) or a Friend Code (12 digits)",
                          alternatives=alternatives)

    # ---- tables

    def _load_table(self, spec: TableSpec) -> Table | None:
        if not self.paks.has(spec.msg):
            return None
        m = msg23.parse(self.paks.read(spec.msg))
        es = self.work.for_path(spec.msg)
        names = [e.name for e in m.entries]
        texts, langs = [], []
        for e in m.entries:
            t = es.get(e.name)
            texts.append(t if t else m.get(e, config.LANG_EN))
            langs.append("es" if t else "en")
        table = Table(names, texts, langs)
        if spec.script is not None:
            table.by_id = self._id_map(spec.script, m)
        return table

    def _id_map(self, script: str, m: msg23.Msg) -> list[int | None]:
        """game id -> entry index from the English script; empty (every id unresolved) if it is missing."""
        if not self.paks.has(script):
            return []
        index = {e.guid: i for i, e in enumerate(m.entries)}
        return [index.get(g) if g else None for g in script_guids(self.paks.read(script))]


def _ability_rule(cost: bool, free: bool):
    return lambda r, token: r._sf2_ability(token, cost=cost, free=free)


# SF2 command name -> rule (insert_numdisp is shared with SF1 and handled before these).
SF2_RULES = {
    "insert_option": InsertResolver._sf2_option,
    "insert_itemname": InsertResolver._sf2_item,
    "insert_itemnamefree": InsertResolver._sf2_item_free,
    "insert_btlwarename": InsertResolver._sf2_card,
    "insert_btlwarenamefree": InsertResolver._sf2_card_free,
    "insert_attackstar": InsertResolver._sf2_star,
    "insert_subaru_name": InsertResolver._sf2_geo,
    "insert_abilityname": _ability_rule(True, False),
    "insert_abilityname_free": _ability_rule(True, True),
    "insert_abilityname_nocost": _ability_rule(False, False),
    "insert_abilityname_nocost_free": _ability_rule(False, True),
    "insert_folder_name": InsertResolver._sf2_folder,
    "insert_emnamefree": InsertResolver._sf2_enemy_free,
    "insert_brother": InsertResolver._sf2_handle,
    "insert_subplayer_plname": InsertResolver._sf2_handle,
    "insert_subaru_profile_name": InsertResolver._sf2_handle,
    "insert_autobrother_name": InsertResolver._sf2_handle,
    "insert_brother_profile_name": InsertResolver._sf2_profile_name,
    "insert_legendcombo_name": InsertResolver._sf2_combo,
    "insert_bestcombo": InsertResolver._sf2_combo,
    "insert_subaru_profile": InsertResolver._sf2_profile,
    "insert_brother_profile": InsertResolver._sf2_profile,
    "insert_wordbox_profile": InsertResolver._sf2_profile,
    "insert_brother_kotoba_free": InsertResolver._sf2_profile,
    "insert_option_profile": InsertResolver._sf2_profile_widest,
}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="inserts", description="Show the assumed width of runtime inserts.")
    ap.add_argument("--scope", default="phase1")
    ap.add_argument("--path", help="pak path of a msg file: use the scope of its game (overrides --scope)")
    ap.add_argument("--slot", type=int, default=0)
    ap.add_argument("--size", type=float, default=60.0)
    ap.add_argument("codes", nargs="+", help="text containing insert codes")
    args = ap.parse_args(argv)
    ps = PakSet()
    scope = scope_of_path(args.path) if args.path else args.scope
    r = InsertResolver(Metrics.load(paks=ps), scope=scope, paks=ps)
    for text in args.codes:
        for tok in codes.tokenize(text):
            if tok.prints == "insert":
                em = r.width_em(tok, args.slot)
                print(f"{em:7.3f} em {em * args.size:7.1f} px  {r.describe(tok)}")
    return 0
