<?php
// hosting/publicar.php — endpoint de la galería pública de ScopePower
//
// Recibe de ScopePower (publicar.py) los ficheros de la galería pública y los deja en la
// carpeta galeria/ del hosting:  galeria/<nombre>/...  y  galeria/_comun/<versión>/...
// Se instala una sola vez en public_html/scopepower/ junto a publicar_config.php, que lleva
// la clave de cada nombre (ver LEEME.md). No hace falta base de datos ni nada más.
//
// Contrato (siempre POST multipart/form-data, respuesta JSON {"ok": true, ...} o
// {"ok": false, "error": "..."} con su código HTTP; cabecera X-ScopePower-Clave obligatoria):
//   accion=probar   slug                        -> {ok, version, raiz_escribible, max_bytes, php}
//   accion=listar   slug, prefijo               -> {ok, ficheros: {"ruta": {"bytes", "sha256"}}}
//                   prefijo: "" (= "<slug>/"), "<slug>/...", "_comun/<versión>/..." o ".htaccess"
//   accion=subir    slug, ruta, sha256, fichero -> {ok, ruta, bytes, sha256}; 413 si pasa de
//                   max_bytes, 422 si el sha256 no coincide con lo recibido
//   accion=borrar   slug, ruta                  -> {ok, existia}
//
// Seguridad: la clave se compara con hash_equals contra la del nombre pedido (un nombre con otra
// clave recibe 401, así nadie pisa la galería de otro); las rutas solo admiten letras, números,
// "_", "-", "." y "/", sin ".." ni segmentos que empiecen por punto (salvo el .htaccess de la raíz
// de galeria/, que lo sube la propia app), siempre dentro de "<slug>/" o "_comun/<versión>/" y
// con extensión de la lista blanca: nada de PHP ni de scripts. Los ficheros se escriben con un
// nombre temporal y rename (atómico). No se devuelven trazas ni rutas del servidor en los errores.

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

const VERSION_ENDPOINT = 1;
const EXTENSIONES = ['jpg', 'jpeg', 'webp', 'png', 'json', 'html', 'js', 'css', 'md', 'txt', 'ico'];
const NOMBRES_EXACTOS = ['LICENSE', 'properties'];
const MAX_LISTADO = 5000;

function responder(int $codigo, array $datos): void
{
    http_response_code($codigo);
    echo json_encode($datos, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function fallo(int $codigo, string $error): void
{
    responder($codigo, ['ok' => false, 'error' => $error]);
}

$ficheroConfig = __DIR__ . DIRECTORY_SEPARATOR . 'publicar_config.php';
if (!is_file($ficheroConfig)) {
    fallo(500, 'Falta publicar_config.php junto a publicar.php');
}
$config = require $ficheroConfig;
$claves = is_array($config['claves'] ?? null) ? $config['claves'] : [];
$raiz = (string)($config['raiz'] ?? (__DIR__ . '/../galeria'));
$maxBytes = (int)($config['max_bytes'] ?? 8 * 1024 * 1024);

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    fallo(405, 'Solo POST');
}
$accion = (string)($_POST['accion'] ?? '');
$slug = (string)($_POST['slug'] ?? '');
if (!preg_match('/^[a-z0-9][a-z0-9-]{1,40}$/', $slug)) {
    fallo(400, 'Nombre no válido');
}
$clave = (string)($_SERVER['HTTP_X_SCOPEPOWER_CLAVE'] ?? '');
$esperada = (string)($claves[$slug] ?? '');
if ($clave === '' || $esperada === '' || !hash_equals($esperada, $clave)) {
    usleep(300000);
    fallo(401, 'Clave rechazada para ese nombre');
}

if (!is_dir($raiz) && !@mkdir($raiz, 0755, true)) {
    fallo(500, 'No se puede crear la carpeta de la galería');
}
$raizReal = realpath($raiz);
if ($raizReal === false) {
    fallo(500, 'La carpeta de la galería no existe');
}

/** Ruta absoluta dentro de galeria/ para una ruta relativa pedida, o null si no se admite. */
function rutaSegura(string $slug, string $ruta, string $raizReal): ?string
{
    if ($ruta === '.htaccess') {
        return $raizReal . DIRECTORY_SEPARATOR . '.htaccess';
    }
    if (strlen($ruta) > 400 || !preg_match('#^[A-Za-z0-9_./-]+$#', $ruta)) {
        return null;
    }
    $segmentos = explode('/', $ruta);
    if (count($segmentos) < 2) {
        return null;
    }
    foreach ($segmentos as $s) {
        if ($s === '' || $s === '.' || $s === '..' || $s[0] === '.') {
            return null;
        }
    }
    if ($segmentos[0] === '_comun') {
        if (count($segmentos) < 3 || !preg_match('/^[0-9][A-Za-z0-9.]*$/', $segmentos[1])) {
            return null;
        }
    } elseif ($segmentos[0] !== $slug) {
        return null;
    }
    $fichero = end($segmentos);
    $ext = strtolower((string)pathinfo($fichero, PATHINFO_EXTENSION));
    if (!in_array($ext, EXTENSIONES, true) && !in_array($fichero, NOMBRES_EXACTOS, true)) {
        return null;
    }
    return $raizReal . DIRECTORY_SEPARATOR . str_replace('/', DIRECTORY_SEPARATOR, $ruta);
}

/** La carpeta (ya creada) del fichero está dentro de galeria/ de verdad (realpath, sin enlaces fuera). */
function dentro(string $absoluta, string $raizReal): bool
{
    $carpeta = realpath(dirname($absoluta));
    if ($carpeta === false) {
        return false;
    }
    return $carpeta === $raizReal || strncmp($carpeta, $raizReal . DIRECTORY_SEPARATOR, strlen($raizReal) + 1) === 0;
}

/** Carpeta relativa (dentro de galeria/) que se puede listar para un prefijo, o null. */
function prefijoSeguro(string $slug, string $prefijo): ?string
{
    if ($prefijo === '') {
        return $slug . '/';
    }
    if ($prefijo === '.htaccess') {
        return '.htaccess';
    }
    if (!preg_match('#^[A-Za-z0-9_./-]+$#', $prefijo) || strpos($prefijo, '..') !== false) {
        return null;
    }
    if (strncmp($prefijo, $slug . '/', strlen($slug) + 1) === 0 || strncmp($prefijo, '_comun/', 7) === 0) {
        return $prefijo;
    }
    return null;
}

switch ($accion) {
    case 'probar':
        responder(200, ['ok' => true, 'version' => VERSION_ENDPOINT, 'raiz_escribible' => is_writable($raizReal),
            'max_bytes' => $maxBytes, 'php' => PHP_VERSION]);
        // no break: responder termina

    case 'listar':
        $prefijo = prefijoSeguro($slug, (string)($_POST['prefijo'] ?? ''));
        if ($prefijo === null) {
            fallo(400, 'Prefijo no admitido');
        }
        $ficheros = [];
        if ($prefijo === '.htaccess') {
            $abs = $raizReal . DIRECTORY_SEPARATOR . '.htaccess';
            if (is_file($abs)) {
                $ficheros['.htaccess'] = ['bytes' => filesize($abs), 'sha256' => hash_file('sha256', $abs)];
            }
            responder(200, ['ok' => true, 'ficheros' => $ficheros]);
        }
        $carpeta = $raizReal . DIRECTORY_SEPARATOR . str_replace('/', DIRECTORY_SEPARATOR, rtrim($prefijo, '/'));
        $carpetaReal = realpath($carpeta);
        if ($carpetaReal === false || !is_dir($carpetaReal) || !dentro($carpetaReal . DIRECTORY_SEPARATOR . 'x', $raizReal)) {
            responder(200, ['ok' => true, 'ficheros' => $ficheros]);
        }
        $it = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($carpetaReal, FilesystemIterator::SKIP_DOTS));
        $n = 0;
        foreach ($it as $f) {
            if (!$f->isFile()) {
                continue;
            }
            $rel = substr($f->getPathname(), strlen($raizReal) + 1);
            $rel = str_replace(DIRECTORY_SEPARATOR, '/', $rel);
            $ficheros[$rel] = ['bytes' => $f->getSize(), 'sha256' => hash_file('sha256', $f->getPathname())];
            if (++$n >= MAX_LISTADO) {
                break;
            }
        }
        responder(200, ['ok' => true, 'ficheros' => $ficheros, 'truncado' => $n >= MAX_LISTADO]);
        // no break

    case 'subir':
        $ruta = (string)($_POST['ruta'] ?? '');
        $sha = strtolower((string)($_POST['sha256'] ?? ''));
        $abs = rutaSegura($slug, $ruta, $raizReal);
        if ($abs === null) {
            fallo(400, 'Ruta no admitida');
        }
        if (!preg_match('/^[0-9a-f]{64}$/', $sha)) {
            fallo(400, 'sha256 no válido');
        }
        $subido = $_FILES['fichero'] ?? null;
        if (!is_array($subido) || !isset($subido['error'])) {
            fallo(400, 'Falta el fichero');
        }
        if ($subido['error'] === UPLOAD_ERR_INI_SIZE || $subido['error'] === UPLOAD_ERR_FORM_SIZE) {
            fallo(413, 'El fichero pasa del límite de subida del hosting');
        }
        if ($subido['error'] !== UPLOAD_ERR_OK || !is_uploaded_file($subido['tmp_name'])) {
            fallo(400, 'Fichero no recibido (código ' . (int)$subido['error'] . ')');
        }
        $bytes = (int)$subido['size'];
        if ($bytes > $maxBytes) {
            @unlink($subido['tmp_name']);
            fallo(413, 'El fichero pasa de max_bytes');
        }
        if (hash_file('sha256', $subido['tmp_name']) !== $sha) {
            @unlink($subido['tmp_name']);
            fallo(422, 'El sha256 no coincide con lo recibido');
        }
        $carpeta = dirname($abs);
        if (!is_dir($carpeta) && !@mkdir($carpeta, 0755, true)) {
            fallo(500, 'No se puede crear la carpeta');
        }
        if (!dentro($abs, $raizReal)) {
            fallo(400, 'Ruta fuera de la galería');
        }
        $temporal = $carpeta . DIRECTORY_SEPARATOR . '.subiendo-' . bin2hex(random_bytes(6));
        if (!move_uploaded_file($subido['tmp_name'], $temporal)) {
            fallo(500, 'No se pudo guardar el fichero');
        }
        @chmod($temporal, 0644);
        if (!rename($temporal, $abs)) {
            @unlink($temporal);
            fallo(500, 'No se pudo colocar el fichero');
        }
        responder(200, ['ok' => true, 'ruta' => $ruta, 'bytes' => $bytes, 'sha256' => $sha]);
        // no break

    case 'borrar':
        $ruta = (string)($_POST['ruta'] ?? '');
        $abs = rutaSegura($slug, $ruta, $raizReal);
        if ($abs === null) {
            fallo(400, 'Ruta no admitida');
        }
        $existia = is_file($abs) && dentro($abs, $raizReal);
        if ($existia && !@unlink($abs)) {
            fallo(500, 'No se pudo borrar');
        }
        responder(200, ['ok' => true, 'existia' => $existia]);
        // no break

    default:
        fallo(400, 'Acción desconocida');
}
