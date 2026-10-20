# Datos de la Luna anotada

Los usa `luna_anotada.py` para poner nombre a los mares, cráteres y montes de las fotos de la Luna.
Se regeneran con `herramientas/preparar_luna.py` a partir de las descargas originales.

## accidentes_luna.csv

Nomenclátor planetario de la Unión Astronómica Internacional (UAI), publicado por el USGS
Astrogeology Science Center: *Gazetteer of Planetary Nomenclature*,
<https://planetarynames.wr.usgs.gov/>. Fichero `MOON_nomenclature_center_pts.zip` de
<https://planetarynames.wr.usgs.gov/GIS_Downloads>, descargado el 20-oct-2026.

Obra del Gobierno de EE. UU., de dominio público. Se quedan los 1 100 nombres de la cara visible y su
borde (hasta 100° del centro): sin los cráteres satélite con letra ni los nombres de los alunizajes
del Apolo, y los cráteres desde 15 km. Columnas: nombre, código de tipo del USGS, latitud y longitud
(grados, este positivo, −180..180) y diámetro en km.

## albedo_luna.jpg

Mapa de color del LROC (Lunar Reconnaissance Orbiter Camera) del *CGI Moon Kit* del NASA Scientific
Visualization Studio, <https://svs.gsfc.nasa.gov/4720>, fichero `lroc_color_poles_1k.jpg`
(1024×512, proyección cilíndrica con la longitud 0 en el centro), pasado a grises.

NASA's Scientific Visualization Studio, de dominio público. Solo sirve para reconocer el giro y el
espejo de la foto por la forma de los mares; no se dibuja.
