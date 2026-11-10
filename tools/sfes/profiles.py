"""File categories and layout profiles (where a text is drawn and how big it may be).

Categories come from the Phase 1 inventory (research/text/categories_summary.csv). Dialogue profiles are
measured from the GUI layouts (research/fonts/gui_textslots.json): SF1 txt_Main_Message = Slot0, 60 px font,
1026 x 205 px region (exactly 3 lines); SF2/SF3 = Slot0, 62 px, 1055 x 226 px.
Everything else uses the 'observed' profile until in-game tests give real box sizes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    slot: int | None        # font slot; None = decided per file (observed)
    size: float | None      # font size in px
    max_px: float | None    # hard per-line limit in px (None = observed from English)
    max_lines: int | None   # hard line limit (None = observed from English)
    hard: bool              # True: limits are errors; False: exceeding English is a warning
    wrap: str               # default wrap mode for entries: 'auto' or 'manual'


PROFILES = {
    "dialogue_sf1": Profile("dialogue_sf1", 0, 60.0, 1026.0, 3, True, "auto"),
    "dialogue_sf2": Profile("dialogue_sf2", 0, 62.0, 1055.0, 3, True, "auto"),
    "dialogue_sf3": Profile("dialogue_sf3", 0, 62.0, 1055.0, 3, True, "auto"),
    "observed_text": Profile("observed_text", None, None, None, None, False, "auto"),
    "observed": Profile("observed", None, None, None, None, False, "manual"),
}

# Categories drawn in the main dialogue window.
DIALOGUE_CATEGORIES = {
    "story_field", "cutscene", "brother_event", "hint_talk", "npc_talk", "field_check", "subevent",
    "field_system", "tutorial", "minigame_event", "humor", "shop", "npc_personal", "broadcast",
}
# Multi-line running text in other windows (wrap automatically against the English maximum).
DESCRIPTION_CATEGORIES = {"card_info", "item_info", "ability_info", "mail_body", "info_text"}
# Never translated.
SKIP_CATEGORIES = {"debug_test", "input_filter"}

GAME_RULES = [
    ("input_filter", r"slang"),
    ("debug_test", r"^messtest"),
    ("remaster_ui", r"^msggui"),
    ("story_field", r"^st[0-9a-f]{4}"),
    ("cutscene", r"^dm[0-9a-f]{2}"),
    ("brother_event", r"brother_demo$"),
    ("hint_talk", r"^rt[0-9a-f]{4}|^talk_chp"),
    ("npc_talk", r"^npc_st"),
    ("field_check", r"^chk_st|^lock_st"),
    ("npc_personal", r"^npcp[ae]?rsonal"),
    ("subevent", r"^subevent"),
    ("field_system", r"^field_|^popup_|^popupinfo"),
    ("tutorial", r"tutorial|tuto"),
    ("card_name", r"^(chip|card|spcard|subcard)name"),
    ("card_info", r"^(chip|card|spcard|subcard)info|^andromeda|^cardforce"),
    ("enemy_name", r"^emname"),
    ("item_name", r"^(item|keyitem)name"),
    ("item_info", r"^iteminf|^keyiteminfo|^iboxdata"),
    ("ability_name", r"^abilityname|^powerupname|^powerup$|^weaponname|^warrock_weapon"),
    ("ability_info", r"^abilityinfo|^powerupinfo|^weaponinfo|^wccinfo"),
    ("mail_title", r"^mailname"),
    ("mail_body", r"^mailinfo|^mail_|^ciphermail"),
    ("broadcast", r"^broadcast"),
    ("humor", r"^humor"),
    ("shop", r"^shop|^numbertrader"),
    ("minigame_event", r"^quiz|^game\d|^wiz_game|^erandgame|^skyhigh|^doubleslot|^btl_|^battle_brother"),
    ("name_list", r"name$|^nickname|^teamname|^foldername|^keyword$|^sservername|^reason_name|^deckname|"
                  r"^popupname"),
    ("info_text", r"_info$|^wizard_info|^reason_info"),
    ("battle_ui", r"^battle"),
    ("menu_screen", r"^sub|scrn|screen|^custom|^cockpit|^deckselect|^wifi|^brother|^title$|^backup|"
                    r"^writebackup|^endingsave|^tenkey|^common$|^ending$"),
]

COMMON_RULES = [
    ("ui_system", r"^common_msggui0000|^common_msggui0006"),
    ("ui_title_mainmenu", r"^common_msggui0001"),
    ("ui_gallery", r"^common_msggui0002"),
    ("ui_settings", r"^common_msggui0003"),
    ("ui_ingame_menu", r"^common_msggui0004"),
    ("ui_network", r"^common_msggui0005|^networkcommon"),
    ("ui_staffroll", r"^common_staffroll"),
    ("ui_ref_tables", r"^refgeneral|^refrr2cardname"),
    ("ui_platform_localize", r"^localize"),
    ("ui_preset_decks", r"^presetdeckmessage"),
    ("ui_savedata", r"^savedata"),
]


def game_of(path: str) -> str:
    """'common', 'sf1', 'sf2' or 'sf3' for a pak path under natives/stm/text/."""
    rest = path.split("natives/stm/text/", 1)[1]
    head = rest.split("/", 1)[0]
    return {"common": "common", "rr1": "sf1", "rr2": "sf2", "rr3": "sf3"}[head]


def short_stem(path: str) -> str:
    name = path.rsplit("/", 1)[1][: -len(".msg.23")]
    name = re.sub(r"^rr[123]_", "", name)
    return re.sub(r"_eng$", "", name)


def category_of(path: str) -> str:
    g = game_of(path)
    if g == "common":
        if "/text/common/message/" in path:
            return "ui_ingame_keyguide"
        s = path.rsplit("/", 1)[1][: -len(".msg.23")]
        rules = COMMON_RULES
    else:
        s = short_stem(path)
        rules = GAME_RULES
    for cat, rx in rules:
        if re.search(rx, s):
            return cat
    return "other"


def profile_for(path: str) -> Profile:
    g = game_of(path)
    cat = category_of(path)
    if g in ("sf1", "sf2", "sf3") and cat in DIALOGUE_CATEGORIES:
        return PROFILES[f"dialogue_{g}"]
    if cat in DESCRIPTION_CATEGORIES:
        return PROFILES["observed_text"]
    return PROFILES["observed"]


# Default font slot for files on observed profiles: menus/HUD/collection UI use Slot2 (BO GSan Serif B);
# dialogue-like windows use Slot0. Used only for em-width comparisons against English in the same file.
def observed_slot(path: str) -> int:
    cat = category_of(path)
    if cat in DESCRIPTION_CATEGORIES or cat in DIALOGUE_CATEGORIES:
        return 0
    return 2
