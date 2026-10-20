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
Visualization Studio, <https://svs.gsfc.nasa.gov/4720>, fichero `lroc_color_poles_2k.tif`
(2048×1024, proyección cilíndrica con la longitud 0 en el centro, unos 5 km por píxel en el
ecuador), pasado a grises.

NASA's Scientific Visualization Studio, de dominio público. Solo sirve para reconocer el giro y el
espejo de la foto por la forma de los mares, y en qué parte de la Luna cae un trozo; no se dibuja.

## contornos_mares.json

Límites de los mares del LROC: *Global Mapping of Lunar Mare Boundaries (65N and 65S)*,
<https://data.lroc.im-ldi.com/lroc/view_rdr/SHAPEFILE_LROC_GLOBAL_MARE>, fichero
`LROC_GLOBAL_MARE_180.ZIP` del PDS de la NASA (sin restricciones de uso), descargado el 20-oct-2026.
Cita: Nelson, D. M. et al. (2014), *Mapping lunar maria extents and lobate scarps using LROC image
products*, Lunar and Planetary Science Conference 45, p. 2861.

De sus 450 000 puntos quedan unos 17 000: solo la cara visible y su borde, sin las manchas de menos de
1° y simplificados a 0,1° (unos 3 km). Es una lista de anillos, cada uno una lista de [latitud,
longitud] en grados. Las rectas que cruzan algunos mares son los límites entre mares vecinos del
propio mapa.
