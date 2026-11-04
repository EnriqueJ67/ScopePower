<?php
// hosting/publicar.php — endpoint de la galería pública de ScopePower
//
// Recibe de ScopePower (publicar.py) los ficheros de la galería pública y los deja en la
// carpeta galeria/ del hosting:  galeria/<nombre>/...  y  galeria/_comun/<versión>/...
// Se instala una sola vez en public_html/scopepower/ junto a publicar_config.php (ver LEEME.md).
// No hace falta base de datos: los nombres registrados y sus claves (cifradas con password_hash)
// viven en claves.php, en esta misma carpeta, que PHP ejecuta sin devolver nada si alguien lo pide.
//
// Nombres: cada propietario es un nombre (minúsculas, números y guiones) con su clave. Con
// 'registro_abierto' el primer ScopePower que use un nombre libre se lo queda (accion=registrar):
// sin altas manuales. El dueño del hosting puede además dar de alta nombres a mano en 'claves'
// de publicar_config.php. Cuotas por nombre y total, tope de nombres y de altas por día, y nombres
// reservados, para que un hosting abierto no se llene.
//
// Quién escribe dónde (el reparto que cierra la puerta a meter código en la web de otro):
//   - Un nombre registrado desde la app solo escribe en galeria/<nombre>/, y ahí solo .jpg y .json:
//     fotos reducidas, la ficha de cada foto, sus anotaciones y el índice galeria.json. Cada fichero
//     se comprueba al llegar (JPEG de verdad y tamaño acotado; JSON que parsea, con solo las claves
//     que escribe publicar.py, y cada foto con las coordenadas del objeto salvo las del Sol y la
//     Luna: un freno a fotos que no salen de ScopePower): lo que no cuadra se rechaza con 422.
//   - Lo compartido (galeria/_comun/<versión>/: motor del planetario, traducciones, JS, CSS y las
//     páginas del visor) y el .htaccess de galeria/ solo los suben o borran el dueño del hosting:
//     las claves de 'claves' de publicar_config.php (alta manual) o los nombres de 'duenos'
//     registrados desde la app (su clave sigue cifrada en claves.php). Para cualquier otro, 403.
//   - Las páginas de cada galería (index.html, planetario.html y sus versiones en en/) las coloca
//     este endpoint: al registrar el nombre y cada vez que llega su galeria.json copia las páginas
//     genéricas de la versión más alta que haya en _comun/<versión>/publico/paginas/. Ningún
//     nombre puede subirlas, listarlas ni borrarlas: así nadie cuelga HTML propio bajo el dominio.
//     (Un ScopePower anterior a 1.2.3 sube sus propias páginas con esos mismos nombres: se le
//     contesta ok con "ignorado": true, no se guarda nada y se colocan las genéricas, para que su
//     cola no se atasque hasta que se actualice.)
//
// Contrato (siempre POST multipart/form-data, respuesta JSON {"ok": true, ...} o
// {"ok": false, "error": "..."} con su código HTTP; cabecera X-ScopePower-Clave obligatoria):
//   accion=registrar slug                        -> {ok, registrado: true|false, cuota_mb, contacto}; 409
//                   nombre en uso con otra clave o reservado; 403 registro cerrado; 429 demasiadas
//                   altas hoy; 507 no caben más galerías. Con la clave de un nombre ya suyo devuelve
//                   registrado=false. En los dos casos deja las páginas del visor en <nombre>/.
//   accion=probar   slug                         -> {ok, version, raiz_escribible, max_bytes, php, usado_mb,
//                   cuota_mb, registro_abierto, contacto, dueno}
//   accion=listar   slug, prefijo                -> {ok, ficheros: {"ruta": {"bytes", "sha256"}}}
//                   prefijo: "" (= "<slug>/"), "<slug>/...", "_comun/<versión>/..." o ".htaccess".
//                   Las páginas que coloca el endpoint en <slug>/ no salen en la lista.
//   accion=subir    slug, ruta, sha256, fichero  -> {ok, ruta, bytes, sha256}; 403 si la ruta no es
//                   de quien la pide (_comun o .htaccess sin ser el dueño), 400 ruta o extensión no
//                   admitida, 413 si pasa de max_bytes, 422 si el sha256 no coincide o el contenido no
//                   es lo que dice ser, 507 si la galería agota su cuota. Un fichero grande llega en
//                   trozos (trozo=i, trozos=n, en orden, con el sha256 del total): se van juntando en
//                   .subiendo-<sha256> y con el último se comprueba y se coloca.
//   accion=borrar   slug, ruta                   -> {ok, existia}; mismos permisos que subir.
//
// Seguridad: la clave se comprueba con hash_equals (claves de la configuración) o password_verify
// (claves registradas); las rutas solo admiten letras, números, "_", "-", "." y "/", sin ".." ni
// segmentos que empiecen por punto (salvo el .htaccess de la raíz de galeria/, que sube el dueño),
// siempre dentro de "<slug>/" o "_comun/<versión>/" y con extensión de la lista blanca: nada de
// PHP ni de scripts. Los ficheros se escriben con un nombre temporal y rename (atómico). No se
// devuelven trazas ni rutas del servidor en los errores.

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

const VERSION_ENDPOINT = 3;
// Lo que admite _comun/<versión>/ (solo el dueño): nada ejecutable en el servidor
const EXTENSIONES_COMUN = ['jpg', 'jpeg', 'webp', 'png', 'json', 'html', 'js', 'css', 'md', 'txt', 'ico'];
const NOMBRES_EXACTOS = ['LICENSE', 'properties'];
// Lo que admite <nombre>/: fotos y datos, nunca páginas ni scripts
const EXTENSIONES_GALERIA = ['jpg', 'json'];
// Las páginas del visor que el endpoint copia de _comun/<versión>/publico/paginas/ a cada galería
const PAGINAS = ['index.html', 'planetario.html', 'en/index.html', 'en/planetario.html'];
const MAX_LISTADO = 5000;
const RESERVADOS = ['_comun', 'comun', 'scopepower', 'admin', 'api', 'galeria', 'www', 'static', 'publico', 'test', 'prueba'];

// ── Lo que se comprueba de cada fichero que llega a <nombre>/ ──
const LADO_MAX_JPG = 1700;                 // publicar.py reduce a 1600 px de lado mayor
const MAX_JSON = 256 * 1024;               // ficha de una foto (<id>.json)
const MAX_JSON_ANOTACIONES = 1024 * 1024;  // <id>_anotaciones.json: el trazado de los mares de la Luna es largo
const MAX_JSON_INDICE = 2 * 1024 * 1024;   // galeria.json: hasta MAX_FOTOS fichas y las series del Sol
const MAX_FOTOS = 1000;
const MAX_TEXTO = 200;                     // cualquier cadena de los JSON (salvo el trazado de los mares)
const MAX_LISTA = 20000;                   // puntos de una curva, manchas, objetos anotados...
// Las categorías de la app (catalogo_offline.CATEGORIAS, en español: el visor las traduce); también vale ""
const CATEGORIAS = ['Galaxia', 'Nebulosa', 'Nebulosa planetaria', 'Resto de supernova', 'Cúmulo abierto',
    'Cúmulo globular', 'Estrella', 'Planeta', 'Luna', 'Sol', 'Cometa', 'Asteroide', 'Meteoro', 'Satélite artificial',
    'Avión', 'Paisaje'];
// Cada foto trae las coordenadas del objeto (ra y dec), salvo las de estos tipos; el Paisaje no se publica
const TIPOS_SIN_COORDENADAS = ['Sol', 'Luna'];
const SIN_COORDENADAS = 'cada foto debe llevar coordenadas (ra y dec) o ser del Sol o la Luna';

// Esquemas por lista blanca (lo que escribe publicar.py: meta_publica, sol_publico, anotaciones_publicas).
// Tipos: texto, num, bool, id, fichero, categoria, textos (lista de textos), puntos (lista de [x, y]),
// trazado (texto largo), ['obj' => [clave => tipo]] y ['lista' => tipo, 'max' => n]. Cualquier valor
// puede ser null (publicar.py escribe null cuando no hay dato).
const ESQUEMA_FOTO = ['obj' => [
    'id' => 'id', 'objeto' => 'texto', 'tipo' => 'categoria', 'fecha' => 'texto', 'fecha_iso' => 'texto',
    'n_fotos' => 'num', 'exposicion' => 'num', 'ra' => 'texto', 'dec' => 'texto',
    'nota' => ['obj' => ['puntuacion' => 'num', 'estrellas' => 'num', 'fwhm' => 'num']],
    'autor' => 'texto', 'mosaico' => 'bool', 'cometa_nucleo' => 'bool',
    'wolf' => ['obj' => ['R' => 'num', 'g' => 'num', 'f' => 'num', 'k' => 'num']],
    'video' => ['obj' => ['frames' => 'num', 'duracion_s' => 'num']],
    'ficheros' => ['obj' => ['jpg' => 'fichero', 'mini' => 'fichero', 'cielo' => 'fichero', 'anotaciones' => 'fichero']],
    'v' => ['obj' => ['jpg' => 'texto', 'mini' => 'texto', 'cielo' => 'texto', 'anotaciones' => 'texto']],
    'ancho' => 'num', 'alto' => 'num',
    'cielo' => ['obj' => ['esquinas' => 'puntos', 'ancho' => 'num', 'alto' => 'num', 'modo' => 'texto', 'area_deg2' => 'num']],
    'anotaciones' => 'texto',
]];
const ESQUEMA_INDICE = ['obj' => [
    'version' => 'num', 'generado' => 'texto', 'app' => 'texto', 'comun' => 'texto', 'autor' => 'texto', 'nombre' => 'texto',
    'idiomas' => 'textos', 'planetario' => 'bool', 'contacto' => 'texto',
    'observador' => ['obj' => ['latitud' => 'num', 'longitud' => 'num', 'redondeo_grados' => 'num']],
    'sol' => ['obj' => [
        'wolf' => ['lista' => ['obj' => ['fecha' => 'texto', 'g' => 'num', 'f' => 'num', 'R' => 'num', 'k' => 'num', 'n' => 'num']]],
        'curva' => ['lista' => ['obj' => ['instante' => 'num', 'r' => 'num']]],
        'orbita' => ['obj' => ['e' => 'num', 'r0' => 'num', 'perihelio' => 'texto', 'n' => 'num', 'dias' => 'num']],
        'diametro' => ['lista' => ['obj' => ['instante' => 'num', 'r_arcsec' => 'num', 'r_efem' => 'num', 'desvio_pct' => 'num',
            'distancia' => 'num', 'fiable' => 'bool']]],
    ]],
    'fotos' => ['lista' => ESQUEMA_FOTO, 'max' => MAX_FOTOS],
]];
const ESQUEMA_ANOTACIONES = ['obj' => [
    'ancho' => 'num', 'alto' => 'num',
    'disco' => ['obj' => ['cx' => 'num', 'cy' => 'num', 'r' => 'num', 'cortado' => 'bool']],
    'sol' => 'bool', 'wolf' => ['obj' => ['g' => 'num', 'f' => 'num', 'R' => 'num', 'k' => 'num']],
    'manchas' => ['lista' => ['obj' => ['x' => 'num', 'y' => 'num', 'r_px' => 'num', 'umbra' => 'bool', 'grupo' => 'num']]],
    'grupos' => ['lista' => ['obj' => ['id' => 'num', 'n' => 'num', 'lat' => 'num', 'dlon' => 'num', 'km2' => 'num',
        'diametro_km' => 'num', 'largo_km' => 'num', 'x' => 'num', 'y' => 'num']]],
    'resolucion_km' => 'num',
    'objetos' => ['lista' => ['obj' => ['nombre' => 'texto', 'angulo' => 'num', 'mag' => 'num', 'fuente' => 'texto', 'familia' => 'texto',
        'luz' => 'bool', 'rango' => 'num', 'x' => 'num', 'y' => 'num', 'a' => 'num', 'b' => 'num']]],
    'luna' => 'bool', 'mares' => 'trazado', 'terminador' => 'puntos', 'iluminada' => 'num', 'dudosa' => 'bool',
]];

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
$registroAbierto = (bool)($config['registro_abierto'] ?? false);
$cuotaNombre = (int)($config['cuota_nombre_mb'] ?? 300) * 1024 * 1024;
$cuotaTotal = (int)($config['cuota_total_mb'] ?? 4000) * 1024 * 1024;
$maxNombres = (int)($config['max_nombres'] ?? 200);
$registrosPorDia = (int)($config['registros_por_dia'] ?? 20);
$contacto = trim((string)($config['contacto'] ?? ''));
$duenos = is_array($config['duenos'] ?? null) ? $config['duenos'] : [];
$reservados = array_merge(RESERVADOS, is_array($config['nombres_reservados'] ?? null) ? $config['nombres_reservados'] : []);
$ficheroClaves = __DIR__ . DIRECTORY_SEPARATOR . 'claves.php';

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    fallo(405, 'Solo POST');
}
$accion = (string)($_POST['accion'] ?? '');
$slug = (string)($_POST['slug'] ?? '');
if (!preg_match('/^[a-z0-9][a-z0-9-]{1,40}$/', $slug)) {
    fallo(400, 'Nombre no válido');
}
$clave = (string)($_SERVER['HTTP_X_SCOPEPOWER_CLAVE'] ?? '');

/** Nombres registrados desde la app: ['nombre' => ['hash' => ..., 'creado' => ts], ...]. */
function cargarClaves(string $fichero): array
{
    if (!is_file($fichero)) {
        return [];
    }
    $datos = include $fichero;
    return is_array($datos) ? $datos : [];
}

function guardarClaves(string $fichero, array $registro): void
{
    $contenido = "<?php\n// Nombres registrados desde ScopePower (claves cifradas). Lo escribe publicar.php; no editar a mano.\nreturn " . var_export($registro, true) . ";\n";
    $temporal = $fichero . '.tmp-' . bin2hex(random_bytes(4));
    if (file_put_contents($temporal, $contenido, LOCK_EX) === false || !rename($temporal, $fichero)) {
        @unlink($temporal);
        fallo(500, 'No se pudo guardar el registro de nombres');
    }
    @chmod($fichero, 0644);
    if (function_exists('opcache_invalidate')) {
        @opcache_invalidate($fichero, true);
    }
}

/** Bytes que ocupa una carpeta (recursivo). */
function tamanoCarpeta(string $carpeta): int
{
    if (!is_dir($carpeta)) {
        return 0;
    }
    $total = 0;
    $it = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($carpeta, FilesystemIterator::SKIP_DOTS));
    foreach ($it as $f) {
        if ($f->isFile()) {
            $total += $f->getSize();
        }
    }
    return $total;
}

/** La carpeta _comun/<versión>/publico/paginas/ de la versión más alta que tenga páginas, o null. */
function carpetaPaginas(string $raizReal): ?string
{
    $comun = $raizReal . DIRECTORY_SEPARATOR . '_comun';
    if (!is_dir($comun)) {
        return null;
    }
    $mejor = null;
    foreach (scandir($comun) ?: [] as $v) {
        if (!preg_match('/^[0-9][A-Za-z0-9.]*$/', $v)) {
            continue;
        }
        $carpeta = $comun . DIRECTORY_SEPARATOR . $v . DIRECTORY_SEPARATOR . 'publico' . DIRECTORY_SEPARATOR . 'paginas';
        if (!is_file($carpeta . DIRECTORY_SEPARATOR . 'index.html')) {
            continue;
        }
        if ($mejor === null || version_compare($v, $mejor[0], '>')) {
            $mejor = [$v, $carpeta];
        }
    }
    return $mejor === null ? null : $mejor[1];
}

/** Copia a <slug>/ las páginas genéricas del visor (solo las que cambian; con nombre temporal y rename). */
function colocarPaginas(string $raizReal, string $slug): void
{
    $origen = carpetaPaginas($raizReal);
    if ($origen === null) {
        return;
    }
    foreach (PAGINAS as $pagina) {
        $de = $origen . DIRECTORY_SEPARATOR . str_replace('/', DIRECTORY_SEPARATOR, $pagina);
        if (!is_file($de)) {
            continue;
        }
        $a = $raizReal . DIRECTORY_SEPARATOR . $slug . DIRECTORY_SEPARATOR . str_replace('/', DIRECTORY_SEPARATOR, $pagina);
        if (is_file($a) && filesize($a) === filesize($de) && hash_file('sha256', $a) === hash_file('sha256', $de)) {
            continue;
        }
        $carpeta = dirname($a);
        if (!is_dir($carpeta) && !@mkdir($carpeta, 0755, true)) {
            continue;
        }
        $temporal = $carpeta . DIRECTORY_SEPARATOR . '.pagina-' . bin2hex(random_bytes(4));
        if (@copy($de, $temporal)) {
            @chmod($temporal, 0644);
            if (!@rename($temporal, $a)) {
                @unlink($temporal);
            }
        }
    }
}

/** ¿Es una lista (claves 0..n-1)? La vacía también (json_decode da [] tanto para {} como para []). */
function esLista(array $a): bool
{
    $i = 0;
    foreach ($a as $k => $v) {
        if ($k !== $i++) {
            return false;
        }
    }
    return true;
}

/** Comprueba un valor contra su esquema (lista blanca). Devuelve qué falla, o null si todo cuadra. */
function comprobar($valor, $esquema, string $donde): ?string
{
    if ($valor === null) {
        return null;
    }
    if (is_string($esquema)) {
        switch ($esquema) {
            case 'num':
                return (is_int($valor) || is_float($valor)) ? null : "$donde no es un número";
            case 'bool':
                return is_bool($valor) ? null : "$donde no es verdadero o falso";
            case 'texto':
                return (is_string($valor) && strlen($valor) <= MAX_TEXTO) ? null : "$donde no es un texto de hasta " . MAX_TEXTO . ' caracteres';
            case 'trazado':
                return is_string($valor) ? null : "$donde no es un texto";
            case 'id':
                return (is_string($valor) && preg_match('/^[A-Za-z0-9_-]{1,200}$/', $valor)) ? null : "$donde no es un identificador válido";
            case 'fichero':
                return (is_string($valor) && preg_match('/^[A-Za-z0-9_-]{1,200}\.(jpg|json)$/', $valor)) ? null : "$donde no es un nombre de fichero .jpg o .json";
            case 'categoria':
                return (is_string($valor) && ($valor === '' || in_array($valor, CATEGORIAS, true))) ? null : "$donde no es un tipo de objeto de la app";
            case 'textos':
                if (!is_array($valor) || !esLista($valor) || count($valor) > MAX_LISTA) {
                    return "$donde no es una lista";
                }
                foreach ($valor as $i => $v) {
                    if (!is_string($v) || strlen($v) > MAX_TEXTO) {
                        return "{$donde}[$i] no es un texto corto";
                    }
                }
                return null;
            case 'puntos':
                if (!is_array($valor) || !esLista($valor) || count($valor) > MAX_LISTA) {
                    return "$donde no es una lista de puntos";
                }
                foreach ($valor as $i => $p) {
                    if (!is_array($p) || count($p) !== 2 || !esLista($p) || !(is_int($p[0]) || is_float($p[0])) || !(is_int($p[1]) || is_float($p[1]))) {
                        return "{$donde}[$i] no es un punto [x, y]";
                    }
                }
                return null;
            default:
                return "$donde: esquema desconocido";
        }
    }
    if (isset($esquema['lista'])) {
        if (!is_array($valor) || !esLista($valor)) {
            return "$donde no es una lista";
        }
        if (count($valor) > ($esquema['max'] ?? MAX_LISTA)) {
            return "$donde tiene más de " . ($esquema['max'] ?? MAX_LISTA) . ' elementos';
        }
        foreach ($valor as $i => $v) {
            $e = comprobar($v, $esquema['lista'], "{$donde}[$i]");
            if ($e !== null) {
                return $e;
            }
        }
        return null;
    }
    if (!is_array($valor) || (count($valor) > 0 && esLista($valor))) {
        return "$donde no es un objeto";
    }
    foreach ($valor as $k => $v) {
        if (!is_string($k) || !array_key_exists($k, $esquema['obj'])) {
            return "$donde tiene una clave que no se publica: " . (is_string($k) ? $k : (string)$k);
        }
        $e = comprobar($v, $esquema['obj'][$k], "$donde.$k");
        if ($e !== null) {
            return $e;
        }
    }
    return null;
}

/** Lo que una ficha tiene que traer además de cuadrar con el esquema: coordenadas, salvo el Sol y la Luna. */
function comprobarFoto(array $foto, string $donde): ?string
{
    $tipo = $foto['tipo'] ?? '';
    if ($tipo === 'Paisaje') {
        return "$donde: las fotos de tipo Paisaje no se publican";
    }
    if (in_array($tipo, TIPOS_SIN_COORDENADAS, true)) {
        return null;
    }
    $ra = $foto['ra'] ?? '';
    $dec = $foto['dec'] ?? '';
    if (!is_string($ra) || trim($ra) === '' || !is_string($dec) || trim($dec) === '') {
        return "$donde: " . SIN_COORDENADAS;
    }
    return null;
}

/** Qué está mal en un fichero que llega a <nombre>/ (JPEG de verdad, JSON por lista blanca), o null si vale. */
function comprobarContenido(string $ruta, string $fichero, int $bytes): ?string
{
    $nombre = basename($ruta);
    $ext = strtolower((string)pathinfo($nombre, PATHINFO_EXTENSION));
    if ($ext === 'jpg') {
        $info = @getimagesize($fichero);
        if ($info === false || ($info[2] ?? 0) !== IMAGETYPE_JPEG) {
            return 'El fichero no es un JPEG';
        }
        if (max((int)$info[0], (int)$info[1]) > LADO_MAX_JPG) {
            return 'La foto pasa de ' . LADO_MAX_JPG . ' px de lado';
        }
        return null;
    }
    $esIndice = $nombre === 'galeria.json';
    $esAnotaciones = substr($nombre, -17) === '_anotaciones.json';
    $maximo = $esIndice ? MAX_JSON_INDICE : ($esAnotaciones ? MAX_JSON_ANOTACIONES : MAX_JSON);
    if ($bytes > $maximo) {
        return "$nombre pasa de " . (int)($maximo / 1024) . ' KB';
    }
    $texto = file_get_contents($fichero);
    $datos = $texto === false ? null : json_decode($texto, true, 16);
    if (!is_array($datos)) {
        return "$nombre no es un JSON válido";
    }
    if ($esIndice) {
        if (!array_key_exists('version', $datos) || !isset($datos['fotos']) || !is_array($datos['fotos'])) {
            return 'galeria.json necesita version y la lista fotos';
        }
        $mal = comprobar($datos, ESQUEMA_INDICE, 'galeria');
        if ($mal !== null) {
            return $mal;
        }
        foreach ($datos['fotos'] as $i => $foto) {
            $mal = comprobarFoto(is_array($foto) ? $foto : [], "galeria.fotos[$i]");
            if ($mal !== null) {
                return $mal;
            }
        }
        return null;
    }
    $donde = pathinfo($nombre, PATHINFO_FILENAME);
    if ($esAnotaciones) {
        return comprobar($datos, ESQUEMA_ANOTACIONES, $donde);
    }
    return comprobar($datos, ESQUEMA_FOTO, $donde) ?? comprobarFoto($datos, $donde);
}

if (!is_dir($raiz) && !@mkdir($raiz, 0755, true)) {
    fallo(500, 'No se puede crear la carpeta de la galería');
}
$raizReal = realpath($raiz);
if ($raizReal === false) {
    fallo(500, 'La carpeta de la galería no existe');
}

// ── Quién es: clave de la configuración (hash_equals) o registrada desde la app (password_verify).
//    Dueño: clave de 'claves', o nombre registrado que está en 'duenos' ──
$registro = cargarClaves($ficheroClaves);
$esperada = (string)($claves[$slug] ?? '');
$entrada = $registro[$slug] ?? null;
$manual = $clave !== '' && $esperada !== '' && hash_equals($esperada, $clave);
$registrada = $clave !== '' && is_array($entrada) && isset($entrada['hash']) && password_verify($clave, (string)$entrada['hash']);
$valida = $manual || $registrada;
$esDueno = $manual || ($registrada && in_array($slug, $duenos, true));

if ($accion === 'registrar') {
    if ($valida) {
        colocarPaginas($raizReal, $slug);
        responder(200, ['ok' => true, 'registrado' => false, 'nombre' => $slug, 'cuota_mb' => (int)($cuotaNombre / 1048576), 'contacto' => $contacto]);
    }
    if ($esperada !== '' || $entrada !== null) {
        usleep(300000);
        fallo(409, 'Ese nombre ya está en uso en este hosting: elige otro');
    }
    if (!$registroAbierto) {
        fallo(403, 'Este hosting no admite altas desde la app: pide el nombre y la clave a su dueño');
    }
    if (in_array($slug, $reservados, true)) {
        fallo(409, 'Ese nombre está reservado: elige otro');
    }
    if (strlen($clave) < 16) {
        fallo(400, 'Clave demasiado corta');
    }
    if (count($registro) >= $maxNombres) {
        fallo(507, 'No caben más galerías en este hosting');
    }
    $hoy = 0;
    foreach ($registro as $r) {
        if ((int)($r['creado'] ?? 0) > time() - 86400) {
            $hoy++;
        }
    }
    if ($hoy >= $registrosPorDia) {
        fallo(429, 'Demasiadas galerías nuevas hoy en este hosting: prueba mañana');
    }
    if (tamanoCarpeta($raizReal) + $cuotaNombre > $cuotaTotal) {
        fallo(507, 'El hosting no tiene sitio para otra galería');
    }
    $registro[$slug] = ['hash' => password_hash($clave, PASSWORD_DEFAULT), 'creado' => time()];
    guardarClaves($ficheroClaves, $registro);
    @mkdir($raizReal . DIRECTORY_SEPARATOR . $slug, 0755);
    colocarPaginas($raizReal, $slug);
    responder(200, ['ok' => true, 'registrado' => true, 'nombre' => $slug, 'cuota_mb' => (int)($cuotaNombre / 1048576), 'contacto' => $contacto]);
}

if (!$valida) {
    usleep(300000);
    fallo(401, 'Clave rechazada para ese nombre');
}

/**
 * Ruta absoluta dentro de galeria/ para una ruta relativa pedida. Si no se admite, responde y
 * termina: 403 cuando la ruta no es de quien la pide (lo común y el .htaccess son del dueño),
 * 400 cuando no vale (fuera de la carpeta, segmento oculto, extensión que no toca).
 */
function rutaSegura(string $slug, string $ruta, string $raizReal, bool $esDueno): string
{
    $soloDueno = 'Solo el dueño del hosting puede subir o borrar lo común (_comun) y el .htaccess';
    if ($ruta === '.htaccess') {
        if (!$esDueno) {
            fallo(403, $soloDueno);
        }
        return $raizReal . DIRECTORY_SEPARATOR . '.htaccess';
    }
    if (strlen($ruta) > 400 || !preg_match('#^[A-Za-z0-9_./-]+$#', $ruta)) {
        fallo(400, 'Ruta no admitida');
    }
    $segmentos = explode('/', $ruta);
    if (count($segmentos) < 2) {
        fallo(400, 'Ruta no admitida');
    }
    foreach ($segmentos as $s) {
        if ($s === '' || $s === '.' || $s === '..' || $s[0] === '.') {
            fallo(400, 'Ruta no admitida');
        }
    }
    $fichero = end($segmentos);
    $ext = strtolower((string)pathinfo($fichero, PATHINFO_EXTENSION));
    if ($segmentos[0] === '_comun') {
        if (!$esDueno) {
            fallo(403, $soloDueno);
        }
        if (count($segmentos) < 3 || !preg_match('/^[0-9][A-Za-z0-9.]*$/', $segmentos[1])) {
            fallo(400, 'Ruta no admitida');
        }
        if (!in_array($ext, EXTENSIONES_COMUN, true) && !in_array($fichero, NOMBRES_EXACTOS, true)) {
            fallo(400, 'Extensión no admitida');
        }
    } else {
        if ($segmentos[0] !== $slug) {
            fallo(403, 'Esa carpeta no es de este nombre');
        }
        if (!in_array($ext, EXTENSIONES_GALERIA, true)) {
            fallo(400, 'En la galería solo se admiten .jpg y .json (las páginas las pone el hosting)');
        }
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
            'max_bytes' => $maxBytes, 'php' => PHP_VERSION, 'registro_abierto' => $registroAbierto,
            'usado_mb' => round(tamanoCarpeta($raizReal . DIRECTORY_SEPARATOR . $slug) / 1048576, 1),
            'cuota_mb' => (int)($cuotaNombre / 1048576), 'contacto' => $contacto, 'dueno' => $esDueno]);
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
        $deGaleria = strncmp($prefijo, '_comun/', 7) !== 0;
        $it = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($carpetaReal, FilesystemIterator::SKIP_DOTS));
        $n = 0;
        foreach ($it as $f) {
            if (!$f->isFile() || $f->getFilename()[0] === '.') {
                continue;
            }
            // Las páginas de <slug>/ las pone el endpoint: para la app no existen (ni las sube ni las borra)
            if ($deGaleria && strtolower($f->getExtension()) === 'html') {
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
        if (strncmp($ruta, $slug . '/', strlen($slug) + 1) === 0 && in_array(substr($ruta, strlen($slug) + 1), PAGINAS, true)) {
            // Las páginas de una app anterior: se aceptan y se descartan; en su sitio van las genéricas
            $subido = $_FILES['fichero'] ?? null;
            if (is_array($subido) && isset($subido['tmp_name'])) {
                @unlink((string)$subido['tmp_name']);
            }
            colocarPaginas($raizReal, $slug);
            responder(200, ['ok' => true, 'ruta' => $ruta, 'ignorado' => true, 'bytes' => 0, 'sha256' => $sha]);
        }
        $abs = rutaSegura($slug, $ruta, $raizReal, $esDueno);
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
        // Cuota de la galería de este nombre (lo común, compartido, no cuenta)
        $deGaleria = strncmp($ruta, '_comun/', 7) !== 0 && $ruta !== '.htaccess';
        if ($deGaleria) {
            $usado = tamanoCarpeta($raizReal . DIRECTORY_SEPARATOR . $slug);
            $viejo = is_file($abs) ? (int)filesize($abs) : 0;
            if ($usado - $viejo + $bytes > $cuotaNombre) {
                @unlink($subido['tmp_name']);
                fallo(507, 'La galería ha agotado su cuota de ' . (int)($cuotaNombre / 1048576) . ' MB en este hosting: retira alguna foto');
            }
        }
        $carpeta = dirname($abs);
        if (!is_dir($carpeta) && !@mkdir($carpeta, 0755, true)) {
            fallo(500, 'No se puede crear la carpeta');
        }
        if (!dentro($abs, $raizReal)) {
            fallo(400, 'Ruta fuera de la galería');
        }
        $trozos = max(1, (int)($_POST['trozos'] ?? 1));
        $trozo = (int)($_POST['trozo'] ?? 0);
        if ($trozos > 1) {
            // Fichero por trozos: se van pegando a .subiendo-<sha256>; el último lo comprueba y lo coloca
            if ($trozo < 0 || $trozo >= $trozos) {
                fallo(400, 'Trozo fuera de rango');
            }
            $parcial = $carpeta . DIRECTORY_SEPARATOR . '.subiendo-' . $sha;
            if ($trozo === 0 && is_file($parcial)) {
                @unlink($parcial);
            }
            if ($trozo > 0 && !is_file($parcial)) {
                fallo(409, 'Falta el principio del fichero: vuelve a empezar por el trozo 0');
            }
            $datos = file_get_contents($subido['tmp_name']);
            @unlink($subido['tmp_name']);
            if ($datos === false || file_put_contents($parcial, $datos, FILE_APPEND | LOCK_EX) === false) {
                fallo(500, 'No se pudo guardar el trozo');
            }
            clearstatcache(true, $parcial);
            if (filesize($parcial) > $maxBytes) {
                @unlink($parcial);
                fallo(413, 'El fichero pasa de max_bytes');
            }
            if ($trozo < $trozos - 1) {
                responder(200, ['ok' => true, 'ruta' => $ruta, 'trozo' => $trozo, 'bytes' => filesize($parcial)]);
            }
            if (hash_file('sha256', $parcial) !== $sha) {
                @unlink($parcial);
                fallo(422, 'El sha256 no coincide con lo recibido');
            }
            $bytes = (int)filesize($parcial);
            $temporal = $parcial;
        } else {
            if (hash_file('sha256', $subido['tmp_name']) !== $sha) {
                @unlink($subido['tmp_name']);
                fallo(422, 'El sha256 no coincide con lo recibido');
            }
            $temporal = $carpeta . DIRECTORY_SEPARATOR . '.subiendo-' . bin2hex(random_bytes(6));
            if (!move_uploaded_file($subido['tmp_name'], $temporal)) {
                fallo(500, 'No se pudo guardar el fichero');
            }
        }
        // Lo que llega a una galería tiene que ser lo que dice ser: un JPEG acotado o un JSON por lista blanca
        if ($deGaleria) {
            $mal = comprobarContenido($ruta, $temporal, $bytes);
            if ($mal !== null) {
                @unlink($temporal);
                fallo(422, $mal);
            }
        }
        @chmod($temporal, 0644);
        if (!rename($temporal, $abs)) {
            @unlink($temporal);
            fallo(500, 'No se pudo colocar el fichero');
        }
        if ($deGaleria && basename($ruta) === 'galeria.json') {
            colocarPaginas($raizReal, $slug);
        }
        responder(200, ['ok' => true, 'ruta' => $ruta, 'bytes' => $bytes, 'sha256' => $sha]);
        // no break

    case 'borrar':
        $ruta = (string)($_POST['ruta'] ?? '');
        $abs = rutaSegura($slug, $ruta, $raizReal, $esDueno);
        $existia = is_file($abs) && dentro($abs, $raizReal);
        if ($existia && !@unlink($abs)) {
            fallo(500, 'No se pudo borrar');
        }
        responder(200, ['ok' => true, 'existia' => $existia]);
        // no break

    default:
        fallo(400, 'Acción desconocida');
}
