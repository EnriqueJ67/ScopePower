<?php
// hosting/publicar_config.example.php — plantilla de publicar_config.php
//
// Copia este fichero como publicar_config.php junto a publicar.php. No va a git.
//
// 'registro_abierto': true = cualquier ScopePower que active la galería pública con un nombre
//           libre se lo queda en el primer uso (la app genera y guarda su clave sola). Los nombres
//           registrados quedan en claves.php (claves cifradas), junto a publicar.php.
//           false = solo los nombres de 'claves' (alta manual).
// 'claves': nombres dados de alta a mano por el dueño del hosting, con su clave en claro. La misma
//           clave va en secretos.ini del ScopePower de esa persona (publicar_clave). Con registro
//           abierto puede quedar vacío.
// 'raiz':   carpeta donde se escribe la galería (se sirve en <url_base>/galeria/).
// 'max_bytes': tamaño máximo por fichero. Los grandes llegan en trozos de 1 MB (por debajo del
//           upload_max_filesize de 2 MB que trae PHP de serie), así que el límite es solo el total.
// 'cuota_nombre_mb': lo que puede ocupar la galería de cada nombre (unas 300 KB por foto más la
//           miniatura y la versión del planetario: 300 MB son unas 600 fotos).
// 'cuota_total_mb': tope de todo galeria/ para admitir nombres nuevos (ajústalo a tu plan).
// 'max_nombres', 'registros_por_dia': frenos a las altas automáticas.
// 'nombres_reservados': además de los fijos (_comun, scopepower, admin, api, galeria, www...).
// 'contacto': correo del dueño del hosting. Sale en el pie de cada galería («Informar de un problema»)
//           para que quien vea algo que no debería estar pueda avisar. Vacío: no sale nada.
//
// Quién escribe dónde: un nombre registrado desde la app solo escribe .jpg y .json en galeria/<nombre>/,
// y cada fichero se comprueba al llegar. Lo común (galeria/_comun/<versión>/: motor del planetario,
// traducciones, JS, CSS y las páginas del visor) y el .htaccess de galeria/ solo los suben las claves
// de 'claves': el ScopePower del dueño, con su clave aquí, lo sube al sincronizar. Las páginas de cada
// galería (index.html, planetario.html y en/) las copia el endpoint desde _comun: nadie sube HTML.

return [
    'registro_abierto' => true,
    'claves' => [
        // 'nombre-de-ejemplo' => 'clave-larga-y-aleatoria',
    ],
    'raiz' => __DIR__ . '/../galeria',
    'max_bytes' => 8 * 1024 * 1024,
    'cuota_nombre_mb' => 300,
    'cuota_total_mb' => 4000,
    'max_nombres' => 200,
    'registros_por_dia' => 20,
    'nombres_reservados' => [],
    'contacto' => '',
];
