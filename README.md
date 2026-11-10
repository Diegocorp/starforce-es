# Mega Man Star Force Legacy Collection en español

Traducción al español latinoamericano de **Mega Man Star Force** (el primer juego) y de los menús de la
colección, para la versión de Steam (Windows y Steam Deck). Gratuita, hecha por fans y no oficial.

## Descarga

Descarga el .zip de la última versión en [Releases](https://github.com/Diegocorp/starforce-es/releases/latest),
o usa el botón verde **Code > Download ZIP** de esta página: los dos traen lo mismo.

## Instalación en Windows

1. Descomprime el .zip en cualquier carpeta.
2. Haz doble clic en **Instalar.bat**.

El instalador busca el juego en Steam, instala REFramework si falta (te pregunta antes), genera los archivos en
español a partir de tu copia del juego y los instala. Para quitar el parche: **Desinstalar.bat**.

## Instalación en Steam Deck

1. En modo escritorio, descarga el .zip y descomprímelo (clic derecho > Extraer > Extraer aquí).
2. En la carpeta del parche, haz clic derecho en un espacio vacío > Abrir terminal aquí, escribe
   `bash Instalar_SteamDeck.sh` y presiona Enter.
3. Solo la primera vez: en Steam, haz clic derecho sobre el juego > Propiedades > General > Configuraciones de
   lanzamiento y escribe `WINEDLLOVERRIDES="dinput8=n,b" %command%` (el instalador te avisa si falta).

Para quitar el parche: `bash Desinstalar_SteamDeck.sh`. El instalador para Steam Deck se probó en Linux, pero
todavía no en un Steam Deck; si algo falla en el tuyo, [repórtalo](https://github.com/Diegocorp/starforce-es/issues).
Los pasos completos, con los controles del modo escritorio, están en [LEEME.txt](LEEME.txt), sección 4.

Si el juego no aparece en español, ve a Settings > General Settings > Language > Text y elige "English".
También funciona con Fluffy Mod Manager (`Crear_mod_Fluffy.bat`). Todos los detalles están en
[LEEME.txt](LEEME.txt).

## Qué incluye

- Mega Man Star Force completo: historia, diálogos, pistas, menús, cartas, objetos, correos y tutoriales.
- Los menús de la colección: menú principal, opciones, galería, música, créditos y juego en red.
- Los signos ¡ y ¿, que la fuente del juego no tenía.

Mega Man Star Force 2 y 3 todavía están en inglés.

## Requisitos

- Una copia legal del juego en Steam (Windows 10 u 11 de 64 bits, o Steam Deck). Versión probada: build 23297979
  (31/10/2026). Con otra versión el parche también se instala; las líneas que el juego haya cambiado se quedan
  en inglés.
- REFramework (gratuito, de código abierto): el instalador lo descarga si lo aceptas.

## Errores y sugerencias

¿Encontraste un error de traducción o algo que no funciona? Abre un reporte en
[Issues](https://github.com/Diegocorp/starforce-es/issues).

## Créditos

Traducción, adaptación, herramientas e instalador: **Diegocorp**.
REFramework: praydog y colaboradores. Python: Python Software Foundation. fontTools: sus autores. Las licencias
están en la carpeta [licencias](licencias).

## Aviso legal

Traducción no oficial, gratuita y sin fines de lucro. No está afiliada ni asociada a Capcom, y Capcom no la
autorizó ni la respalda. Mega Man, Mega Man Star Force y todos los nombres y marcas relacionados pertenecen a
CAPCOM CO., LTD. Este repositorio no contiene archivos del juego: el instalador los genera en tu PC a partir de
tu propia copia. Prohibida su venta.

---

Las herramientas para mantener la traducción están en [tools](tools) (en inglés).
