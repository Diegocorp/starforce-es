# Mega Man Star Force Legacy Collection en español

**[Español](#español)** | **[English](#english)**

---

## Español

Traducción al español latinoamericano de los tres juegos (**Mega Man Star Force**, **Star Force 2** y **Star Force
3**) y de los menús de la colección, para la versión de Steam (Windows y Steam Deck). Gratuita, hecha por fans y no oficial.

### Descarga

Descarga el .zip de la última versión en [Releases](https://github.com/Diegocorp/starforce-es/releases/latest),
o usa el botón verde **Code > Download ZIP** de esta página: los dos traen lo mismo.

### Instalación en Windows

1. Descomprime el .zip en cualquier carpeta.
2. Haz doble clic en **Instalar.bat**.

El instalador busca el juego en Steam, instala REFramework si falta (te pregunta antes), genera los archivos en
español a partir de tu copia del juego y los instala. Para quitar el parche: **Desinstalar.bat**.

### Instalación en Steam Deck

1. En modo escritorio, descarga el .zip y descomprímelo (clic derecho > Extraer > Extraer aquí).
2. En la carpeta del parche, haz clic derecho en un espacio vacío > Abrir terminal aquí, escribe
   `bash Instalar_SteamDeck.sh` y presiona Enter.
3. Solo la primera vez: en Steam, haz clic derecho sobre el juego > Propiedades > General > Configuraciones de
   lanzamiento y escribe `WINEDLLOVERRIDES="dinput8=n,b" %command%` (el instalador te avisa si falta).

Para quitar el parche: `bash Desinstalar_SteamDeck.sh`. Si algo falla en tu Steam Deck,
[repórtalo](https://github.com/Diegocorp/starforce-es/issues).
Los pasos completos, con los controles del modo escritorio, están en [LEEME.txt](LEEME.txt), sección 4.

Si el juego no aparece en español, ve a Settings > General Settings > Language > Text y elige "English".
También funciona con Fluffy Mod Manager (`Crear_mod_Fluffy.bat`). Todos los detalles están en
[LEEME.txt](LEEME.txt).

### Qué incluye

- Mega Man Star Force completo: historia, diálogos, pistas, menús, cartas, objetos, correos y tutoriales.
- Mega Man Star Force 2 completo: historia, diálogos, pistas, menús, cartas, objetos, correos, páginas personales y
  tutoriales.
- Mega Man Star Force 3 completo (nuevo en la versión 1.3): historia, diálogos, pistas, menús, cartas, habilidades,
  objetos clave, correos, misiones y tutoriales.
- Los menús de la colección: menú principal, opciones, galería, música, créditos y juego en red.
- Los signos ¡ y ¿, que la fuente del juego no tenía.

Sigue en inglés el texto dibujado dentro de imágenes (algunos títulos y logotipos).

### Requisitos

- Una copia legal del juego en Steam (Windows 10 u 11 de 64 bits, o Steam Deck). Versión probada: build 23297979
  (31/10/2026). Con otra versión el parche también se instala; las líneas que el juego haya cambiado se quedan
  en inglés.
- REFramework (gratuito, de código abierto): el instalador lo descarga si lo aceptas.

### Errores y sugerencias

¿Encontraste un error de traducción o algo que no funciona? Abre un reporte en
[Issues](https://github.com/Diegocorp/starforce-es/issues).

### Créditos

Traducción, adaptación, herramientas e instalador: **Diegocorp**.
REFramework: praydog y colaboradores. Python: Python Software Foundation. fontTools: sus autores. Las licencias
están en la carpeta [licencias](licencias).

### Aviso legal

Traducción no oficial, gratuita y sin fines de lucro. No está afiliada ni asociada a Capcom, y Capcom no la
autorizó ni la respalda. Mega Man, Mega Man Star Force y todos los nombres y marcas relacionados pertenecen a
CAPCOM CO., LTD. Este repositorio no contiene archivos del juego: el instalador los genera en tu PC a partir de
tu propia copia. Prohibida su venta.

Las herramientas para mantener la traducción están en [tools](tools) (en inglés).

---

## English

A Latin American Spanish translation of all three games (**Mega Man Star Force**, **Star Force 2** and **Star Force
3**) and of the collection's menus, for the Steam version (Windows and Steam Deck). Free, fan-made and unofficial.

### Download

Download the .zip of the latest version from [Releases](https://github.com/Diegocorp/starforce-es/releases/latest),
or use the green **Code > Download ZIP** button on this page: both contain the same files.

### Installing on Windows

1. Extract the .zip to any folder.
2. Double-click **Instalar.bat**.

The installer finds the game in your Steam libraries, installs REFramework if it is missing (it asks first),
generates the Spanish files from your own copy of the game and installs them. To remove the patch, run
**Desinstalar.bat**.

### Installing on Steam Deck

1. In Desktop Mode, download the .zip and extract it (right-click > Extract > Extract here).
2. In the patch folder, right-click an empty space > Open Terminal Here, type `bash Instalar_SteamDeck.sh` and
   press Enter.
3. First time only: in Steam, right-click the game > Properties > General > Launch Options and enter
   `WINEDLLOVERRIDES="dinput8=n,b" %command%` (the installer warns you if it is missing).

To remove the patch, run `bash Desinstalar_SteamDeck.sh`. If something goes wrong on your Steam Deck, please
[report it](https://github.com/Diegocorp/starforce-es/issues).
The full steps, including the Desktop Mode controls, are in [LEEME.txt](LEEME.txt), section 4 (in Spanish).

The patch replaces the English text, so the game must be set to English: if it does not show up in Spanish, go to
Settings > General Settings > Language > Text and choose "English". The patch also works with Fluffy Mod Manager
(`Crear_mod_Fluffy.bat`). All the details are in [LEEME.txt](LEEME.txt) (in Spanish).

### What is included

- Mega Man Star Force, complete: story, dialogue, hints, menus, cards, items, mail and tutorials.
- Mega Man Star Force 2, complete: story, dialogue, hints, menus, cards, items, mail, personal pages and tutorials.
- Mega Man Star Force 3, complete (new in version 1.3): story, dialogue, hints, menus, cards, abilities, key items,
  mail, requests and tutorials.
- The collection's menus: main menu, options, gallery, music, credits and online play.
- The ¡ and ¿ characters, which the game's font did not have.

Text drawn inside images (some titles and logos) stays in English.

### Requirements

- A legal copy of the game on Steam (64-bit Windows 10 or 11, or Steam Deck). Tested game version: build 23297979
  (2026-10-31). The patch also installs on other versions; any lines the game has changed stay in English.
- REFramework (free and open source): the installer downloads it if you agree.

### Bugs and suggestions

Found a translation mistake or something that does not work? Open a report in
[Issues](https://github.com/Diegocorp/starforce-es/issues). Reports in English or Spanish are welcome.

### Credits

Translation, adaptation, tools and installer: **Diegocorp**.
REFramework: praydog and contributors. Python: Python Software Foundation. fontTools: its authors. The licenses are
in the [licencias](licencias) folder.

### Legal notice

Unofficial, free and non-profit translation. It is not affiliated or associated with Capcom, and Capcom has not
authorized or endorsed it. Mega Man, Mega Man Star Force and all related names and trademarks belong to
CAPCOM CO., LTD. This repository contains no game files: the installer generates them on your PC from your own
copy of the game. Not for sale.

The tools used to maintain the translation are in [tools](tools).
