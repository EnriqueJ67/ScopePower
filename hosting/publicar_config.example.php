<?php
// hosting/publicar_config.example.php — plantilla de publicar_config.php
//
// Copia este fichero como publicar_config.php junto a publicar.php y rellena las claves.
// publicar_config.php lleva secretos: no va a git (.gitignore) ni se comparte.
//
// 'claves': un nombre (el de [Publicar] nombre en ScopePower, o el de [Observatorio] autor
//           pasado a minúsculas sin tildes ni espacios) por cada propietario que publique en
//           este hosting, con su clave. La misma clave va en secretos.ini de ese ScopePower
//           (publicar_clave). Una clave larga y aleatoria, por ejemplo:
//               python -c "import secrets; print(secrets.token_urlsafe(32))"
// 'raiz':   carpeta donde se escribe la galería (se sirve en <url_base>/galeria/).
// 'max_bytes': tamaño máximo por fichero. El mayor que sube ScopePower es el catálogo de
//           objetos del planetario (3,5 MB). Los ficheros grandes llegan en trozos de 1 MB,
//           por debajo del upload_max_filesize de 2 MB que trae PHP de serie.

return [
    'claves' => [
        'nombre-de-ejemplo' => 'CAMBIA-ESTA-CLAVE-por-una-larga-y-aleatoria',
    ],
    'raiz' => __DIR__ . '/../galeria',
    'max_bytes' => 8 * 1024 * 1024,
];
