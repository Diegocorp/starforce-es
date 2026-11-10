r"""Instalador del parche en español de Mega Man Star Force Legacy Collection.

Uso (lo llaman Instalar.bat, Desinstalar.bat, Crear_mod_Fluffy.bat y, en Steam Deck / Linux,
Instalar_SteamDeck.sh y Desinstalar_SteamDeck.sh):
  instalador.py instalar    [CARPETA_DEL_JUEGO] [--juego CARPETA] [--si]
  instalador.py desinstalar [CARPETA_DEL_JUEGO] [--juego CARPETA] [--si]
  instalador.py fluffy      [CARPETA_DEL_JUEGO] [--juego CARPETA] [--si] [--salida ARCHIVO.zip]
  instalador.py comprobar   [CARPETA_DEL_JUEGO] [--juego CARPETA]   (genera los archivos en memoria, no escribe nada)
--si responde que sí a todas las preguntas. La carpeta del juego también se puede arrastrar sobre el .bat.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCH_DIR = HERE.parent
DATA = HERE / "datos" / "traduccion_es.json.gz"
LOG = PATCH_DIR / "registro.txt"
AUTHOR = "Equipo de traducción"          # replaced by the packager with the credits name

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
if sys.version_info < (3, 11):
    sys.exit(f"Este instalador necesita Python 3.11 o una versión más reciente (este sistema tiene "
             f"{sys.version.split()[0]}).")
from sfes import parche  # noqa: E402

WIN = parche.IS_WINDOWS
INSTALL = "Instalar.bat" if WIN else "Instalar_SteamDeck.sh"
UNINSTALL = "Desinstalar.bat" if WIN else "Desinstalar_SteamDeck.sh"
SEP = "\\" if WIN else "/"

_log_lines: list[str] = []
_write_log = True


class Cancelado(Exception):
    """El jugador eligió no seguir (no es un error)."""


def say(text: str = "") -> None:
    print(text, flush=True)
    _log_lines.append(text)


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def ask(question: str, auto_yes: bool) -> bool:
    if auto_yes:
        say(f"{question} [S/N] S (automático)")
        return True
    while True:
        try:
            ans = input(f"{question} [S/N] ").strip().lower()
        except EOFError:
            ans = "n"
        _log_lines.append(f"{question} [S/N] {ans}")
        if ans in ("s", "si", "sí", "y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        say("Escribe S o N.")


def write_log() -> None:
    if not _write_log:
        return
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now().isoformat(timespec='seconds')} =====\n")
            f.write("\n".join(_log_lines) + "\n")
    except OSError:
        pass


def fecha(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y a las %H:%M")
    except (TypeError, ValueError):
        return "fecha desconocida"


def choose_game(arg: str | None, auto_yes: bool) -> Path:
    if arg:
        p = Path(arg.strip().strip('"'))
        if not parche.is_game_dir(p):
            raise parche.ParcheError(f"No encontré el juego en: {p}\n"
                                     f"(La carpeta debe contener {parche.GAME_EXE} y {parche.BASE_PAK}.)")
        return p
    found = parche.find_game_dirs()
    if len(found) == 1:
        say(f"Juego encontrado en: {found[0]}")
        return found[0]
    if len(found) > 1:
        say("Encontré el juego en varias carpetas:")
        for i, p in enumerate(found, 1):
            say(f"  {i}. {p}")
        if auto_yes:
            return found[0]
        while True:
            ans = input("Escribe el número de la carpeta que quieres usar: ").strip()
            if ans.isdigit() and 1 <= int(ans) <= len(found):
                return found[int(ans) - 1]
            say("Escribe uno de los números de la lista.")
    say("No encontré el juego en las bibliotecas de Steam.")
    if auto_yes:
        raise parche.ParcheError("Indica la carpeta del juego con --juego.")
    while True:
        try:
            ans = input("Pega aquí la ruta de la carpeta del juego (o presiona Enter sin escribir nada para salir): ")
        except EOFError:
            ans = ""
        ans = ans.strip().strip('"')
        if not ans:
            raise Cancelado()
        if parche.is_game_dir(Path(ans)):
            return Path(ans)
        say(f"Esa carpeta no tiene {parche.GAME_EXE} y {parche.BASE_PAK}. Inténtalo otra vez.")


def check_running() -> None:
    if parche.game_running():
        raise parche.ParcheError("El juego está abierto. Ciérralo y vuelve a ejecutar este archivo.")


def check_version(game: Path, data: dict, auto_yes: bool) -> None:
    tested = data["game"].get("buildid")
    current = parche.game_buildid(game)
    if current and tested and current != tested:
        say(f"AVISO: tu juego tiene la build {current} de Steam y el parche se probó con la build {tested}.")
        say("Las líneas que el juego haya cambiado se quedarán en inglés; el resto se traduce igual.")
        if not ask("¿Quieres continuar?", auto_yes):
            raise Cancelado()


MENU_KEY = "la tecla Insert" if WIN else "L3 + R3"


def check_ref(game: Path, auto_yes: bool, can_enable: bool) -> tuple[bool, bool]:
    """Revisa REFramework. Devuelve (se cambió re2_fw_config.txt, el jugador quiere que se descargue REFramework).
    La descarga se hace después de instalar los archivos del parche, para que quede anotada en la lista de la
    instalación y nunca quede REFramework instalado si la generación falla."""
    ref = parche.reframework_state(game)
    if not ref.dll:
        say("No encontré REFramework (dinput8.dll) en la carpeta del juego. El juego lo necesita para cargar el parche.")
        if can_enable and ask(f"¿Quieres que lo descargue e instale (versión probada {parche.REF_VERSION}, de "
                              "github.com/praydog/REFramework-nightly)?", auto_yes):
            return False, True
        say("Sin REFramework el juego no carga el parche. Si prefieres instalarlo a mano, lee LEEME.txt, sección 2 "
            "(Requisitos).")
        if can_enable and not ask("¿Quieres instalar los archivos del parche de todos modos?", auto_yes):
            raise Cancelado()
        return False, False
    if ref.loose_enabled is True:
        return False, False
    say("El 'Loose File Loader' de REFramework está desactivado; el parche lo necesita.")
    if ask("¿Quieres que lo active ahora (en re2_fw_config.txt)?", auto_yes):
        parche.enable_loose_loader(game, menu_closed=not WIN)
        say("  Activado.")
        return True, False
    say(f"  No se activó. Actívalo en el menú de REFramework (se abre con {MENU_KEY}) > LooseFileLoader >")
    say("  \"Enable Loose File Loader\" y reinicia el juego.")
    return False, False


def get_reframework(game: Path) -> bool:
    say("Descargando REFramework...")
    try:
        parche.install_reframework(game, parche.download_reframework())
    except parche.ParcheError as exc:
        say(f"  {exc}")
        say("  Los archivos del parche quedaron instalados; el juego los cargará cuando instales REFramework.")
        return False
    parche.enable_loose_loader(game, menu_closed=True)
    parche.update_manifest(game, reframework_installed=True, reframework_config_changed=True)
    say(f"  REFramework instalado y configurado (su menú se abre con {MENU_KEY}).")
    return True


def check_launch_option() -> None:
    """Steam Deck / Linux: Proton solo carga REFramework con una opción de lanzamiento que hay que poner a mano."""
    if WIN:
        return
    ok = parche.launch_option_ok(parche.launch_options())
    if ok:
        say("La opción de lanzamiento de Proton para REFramework ya está configurada en Steam.")
        return
    say("")
    say("FALTA UN PASO (solo la primera vez): en Steam, haz clic derecho sobre el juego > Propiedades >")
    say("General > Configuraciones de lanzamiento y escribe exactamente:")
    say(f"    {parche.LAUNCH_OPTION}")
    say("Sin esa línea, Proton no carga REFramework y el juego sigue en inglés.")


def check_installed_build(game: Path, manifest: dict | None) -> None:
    if not manifest:
        return
    built_for, current = manifest.get("game_build"), parche.game_buildid(game)
    if built_for and current and built_for != current:
        say(f"AVISO: el parche instalado se generó para la build {built_for} y tu juego ahora tiene la build "
            f"{current} (Steam lo actualizó).")
        say(f"Vuelve a ejecutar {INSTALL} para generar los archivos a partir de la versión nueva.")


def generate(game: Path, data: dict) -> tuple[dict, parche.GenReport]:
    say("Generando los archivos en español a partir de tu copia del juego (tarda menos de un minuto)...")

    def progress(i: int, n: int) -> None:
        print(f"\r  {i} de {n} archivos de texto", end="", flush=True)

    files, rep = parche.generate(game, data, progress)
    print()
    say(f"  Archivos generados: {rep.files} (líneas en español: {rep.entries}).")
    if rep.entries_stale or rep.entries_missing:
        say(f"  Líneas que cambiaron en tu versión del juego y se quedan en inglés: "
            f"{rep.entries_stale + rep.entries_missing}.")
    if rep.files_missing:
        say(f"  Archivos de texto que no existen en tu versión y se omitieron: {len(rep.files_missing)}.")
    if rep.font_error:
        say(f"  AVISO: no se pudo preparar la fuente con ¡ y ¿ ({rep.font_error}). El texto se instala igual, "
            "pero esos signos pueden no verse.")
    total = len(data["files"]) + len(data["fonts"])
    if rep.files_as_tested + bool(rep.font_as_tested) == total:
        say("  Todos los archivos son idénticos a los de la versión probada.")
    return files, rep


def no_files(game: Path) -> parche.ParcheError:
    msg = "No se generó ningún archivo: tu versión del juego no es compatible con este parche."
    if parche.read_manifest(game) is not None:
        msg += f" Hay una instalación anterior del parche: ejecuta {UNINSTALL}."
    return parche.ParcheError(msg)


def cmd_install(a) -> int:
    data = parche.load_data(DATA)
    say(f"{data['name']}, versión {data['version']}")
    say("")
    game = choose_game(a.juego, a.si)
    check_running()
    check_version(game, data, a.si)
    ref_changed, want_ref = check_ref(game, a.si, can_enable=True)
    try:
        old = parche.read_manifest(game)
    except parche.ParcheError:
        old = None
        say("La lista de la instalación anterior está dañada: se reparará con esta instalación.")
    if old is not None:
        say(f"Ya hay una instalación del parche (versión {old.get('version')}). Se reemplazará.")
    files, gen = generate(game, data)
    if not files:
        raise no_files(game)
    rep = parche.install(game, data, files, gen, ref_config_changed=ref_changed)
    say(f"Se instalaron {plural(rep.written, 'archivo', 'archivos')} en {game / 'natives'}.")
    if rep.backed_up:
        say(f"Archivos sueltos de otros mods respaldados: {rep.backed_up} (en "
            f"{game / parche.STATE_DIR / parche.BACKUP_DIR}). Se restaurarán al desinstalar.")
    if want_ref:
        get_reframework(game)
    check_launch_option()
    say("")
    say("Listo. El texto del juego tiene que estar en inglés: si los menús no aparecen en español, ve a")
    say("Settings > General Settings > Language > Text (Opciones > Opciones generales > Idioma > Texto)")
    say("y elige \"English\".")
    return 0


def cmd_uninstall(a) -> int:
    game = choose_game(a.juego, a.si)
    check_running()
    manifest = parche.read_manifest(game)
    if manifest is None:
        say(f"El parche no está instalado en esta carpeta (no hay lista de instalación en sfes_es{SEP}).")
        return 0
    check_installed_build(game, manifest)
    if manifest.get("residual"):
        say("Quedan archivos de una desinstalación anterior que otro mod había cambiado; se revisan otra vez.")
    else:
        say(f"Se quitará el parche (versión {manifest.get('version')}, instalado el "
            f"{fecha(manifest.get('installed'))}).")
    if not ask("¿Quieres continuar?", a.si):
        raise Cancelado()
    rep = parche.uninstall(game)
    say(f"Archivos del parche quitados: {rep.removed}. Archivos de otros mods restaurados: {rep.restored}.")
    if rep.kept_changed:
        say(f"{plural(len(rep.kept_changed), 'archivo cambió', 'archivos cambiaron')} desde la instalación (por "
            f"otro mod o un gestor de mods) y se dejaron como están; sus respaldos siguen en sfes_es{SEP}respaldo:")
        for p in rep.kept_changed[:20]:
            say(f"  {p}")
        if len(rep.kept_changed) > 20:
            say(f"  ... y {len(rep.kept_changed) - 20} más (lista completa en sfes_es{SEP}manifiesto.json).")
    elif rep.leftover_backups:
        say(f"Quedan respaldos de otros mods en {game / parche.STATE_DIR / parche.BACKUP_DIR}: revísalos.")
    else:
        say("El juego vuelve a estar en inglés.")
    if manifest.get("reframework_installed") and not rep.kept_changed:
        if ask("El parche instaló REFramework. ¿Quieres quitarlo también (dinput8.dll)?", a.si):
            status = parche.remove_reframework(game)
            if status == "removed":
                say(f"Se quitó REFramework. Su configuración (re2_fw_config.txt) y su carpeta reframework{SEP}, si "
                    "existe, se quedan por si otros mods las usan.")
                if not WIN:
                    say(f"Ya puedes borrar la opción de lanzamiento {parche.LAUNCH_OPTION} en Steam (Propiedades > "
                        "General > Configuraciones de lanzamiento).")
            elif status == "missing":
                say("dinput8.dll ya no está en la carpeta del juego; no había nada que quitar.")
            else:
                say("dinput8.dll cambió desde la instalación (quizás instalaste otra versión de REFramework), así que "
                    "no se quitó.")
            return 0
    if manifest.get("reframework_config_changed"):
        say("El 'Loose File Loader' de REFramework sigue activado (otros mods pueden usarlo).")
    say("REFramework no se desinstala: si ya no lo usas, borra dinput8.dll de la carpeta del juego.")
    return 0


def cmd_fluffy(a) -> int:
    data = parche.load_data(DATA)
    say(f"{data['name']}, versión {data['version']} (paquete para Fluffy Mod Manager)")
    game = choose_game(a.juego, a.si)
    check_version(game, data, a.si)
    check_ref(game, a.si, can_enable=False)
    files, _ = generate(game, data)
    if not files:
        raise no_files(game)
    out = Path(a.salida) if a.salida else PATCH_DIR / f"MMSFLC_Espanol_v{data['version']}_Fluffy.zip"
    parche.fluffy_zip(out, data, files, AUTHOR)
    say(f"Paquete creado: {out}")
    say("Agrégalo en Fluffy Mod Manager (arrastra el .zip a la ventana con el juego elegido) y actívalo.")
    say("Necesitas REFramework con el Loose File Loader activado y el texto del juego en inglés (LEEME.txt).")
    say("El paquete contiene archivos generados a partir de tu copia del juego: es solo para tu uso, no lo compartas.")
    return 0


def cmd_check(a) -> int:
    global _write_log
    _write_log = False                   # comprobar no escribe nada, tampoco registro.txt
    data = parche.load_data(DATA)
    game = choose_game(a.juego, True)
    check_installed_build(game, parche.read_manifest(game))
    files, rep = generate(game, data)
    say(f"Comprobación terminada: se generaron {plural(len(files), 'archivo', 'archivos')} en memoria; no se "
        "escribió nada.")
    return 0 if files and not rep.font_error else 1


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if WIN:
            raise parche.ParcheError("No entendí la orden. Haz doble clic en el .bat (o arrastra solo la carpeta del "
                                     "juego sobre él) y el instalador hará el resto.")
        raise parche.ParcheError(f"No entendí la orden. Escribe solo bash {INSTALL} (o bash {UNINSTALL}) y el "
                                 "instalador hará el resto.")


def main(argv: list[str] | None = None) -> int:
    ap = _Parser(prog="instalador", description="Parche en español de MMSF Legacy Collection")
    ap.add_argument("orden", choices=["instalar", "desinstalar", "fluffy", "comprobar"])
    ap.add_argument("carpeta", nargs="?", help="carpeta del juego (también se puede arrastrar sobre el .bat)")
    ap.add_argument("--juego", help="carpeta del juego")
    ap.add_argument("--si", action="store_true", help="responder que sí a todo")
    ap.add_argument("--salida", help="archivo .zip para Fluffy Mod Manager")
    try:
        a = ap.parse_args(argv)
        a.juego = a.juego or a.carpeta
        return {"instalar": cmd_install, "desinstalar": cmd_uninstall, "fluffy": cmd_fluffy,
                "comprobar": cmd_check}[a.orden](a)
    except Cancelado:
        say("Cancelado. No se hizo ningún cambio.")
        return 1
    except parche.ParcheError as exc:
        say(f"ERROR: {exc}")
        return 2
    except PermissionError as exc:
        say(f"ERROR: El sistema no permitió modificar un archivo ({exc}).")
        if parche.IS_WINDOWS:
            say("Cierra el juego (y Steam, si hace falta) y vuelve a intentarlo. Si el archivo es de solo lectura,")
            say("quita esa opción en sus propiedades; si sigue fallando, haz clic derecho en el .bat y elige")
            say("'Ejecutar como administrador'.")
        else:
            say("Cierra el juego y vuelve a intentarlo. Revisa que la carpeta del juego no sea de solo lectura.")
        return 3
    except Exception:
        say("ERROR inesperado. Los detalles están abajo y en registro.txt (en la carpeta del parche):")
        say(traceback.format_exc())
        return 4
    finally:
        write_log()


if __name__ == "__main__":
    sys.exit(main())
