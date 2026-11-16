#!/usr/bin/env php
<?php

declare(strict_types=1);

/**
 * php_reference.php -- an independent reference resolver for PHP and Blade.
 *
 * It is one half of the Wingbox PHP benchmark (benchmarks/php); it shares no code
 * with Wingbox and uses nothing outside the PHP standard library: token_get_all,
 * json_decode/json_encode and the file functions. There is no Composer package,
 * no autoloader, no shell call and no network access.
 *
 * Usage:
 *   php php_reference.php <files.json> <out.json> [--root DIR]
 *
 * <files.json> is a JSON list of the absolute paths of the PHP and Blade source
 * files of one checkout. --root is the checkout root; without it the common
 * directory of the listed files is used. Every path written to <out.json> is
 * relative to the root, so the output names no directory of the machine that
 * produced it.
 *
 * What it resolves, from the source text alone:
 *
 *   PHP classes
 *     - namespace declarations (inline and braced, several per file);
 *     - `use` statements, including aliases, comma lists and group syntax, and
 *       `use function` / `use const` (ignored: they name no class);
 *     - declarations of class, interface, trait and enum (anonymous classes and
 *       `X::class` are not declarations);
 *     - references to a class: `new X`, `X::member`, `X::class`, `extends`,
 *       `implements`, trait `use`, type hints of parameters, properties and
 *       return types (union, intersection and nullable included), `catch`,
 *       `instanceof` and attributes. `self`, `static` and `parent` name the
 *       current class, not another file, and are left out.
 *     Names follow the PHP rules: an alias first, then the current namespace.
 *     The class is then resolved to a file through the declarations of the
 *     checkout, or, when no declaration exists, through the PSR-4 maps of every
 *     composer.json in the tree (autoload and autoload-dev, the longest prefix
 *     wins). A file that declares a class wins over the PSR-4 path for that
 *     class. When the same class is declared in several files, the file the
 *     PSR-4 map names for it wins; if that leaves zero or several files, the
 *     name is ambiguous and resolves to nothing. (A generated IDE-helper file
 *     declaring stub classes for every model is the usual case.)
 *
 *   Blade views
 *     - @extends, @include, @includeIf, @includeWhen, @includeUnless,
 *       @includeFirst, @component, @each and @livewire;
 *     - <x-name>, <x-namespace::name>, <x-dynamic-component> with a literal
 *       component name, <livewire:alias>;
 *     - `view('a.b')`, `View::make('a.b')` and `view()->make('a.b')` in PHP.
 *     View roots are every `resources/views` directory of the checkout plus the
 *     directories of `loadViewsFrom(<path>, '<namespace>')` and
 *     `View::addNamespace('<namespace>', <path>)` calls, with the usual
 *     `<module>/resources/views` convention (namespace is the kebab-case module
 *     directory); a namespace no registration declares is read as the directory
 *     of that name under a default root. A view that resolves in several roots
 *     is ambiguous and resolves to nothing.
 *
 *   Livewire aliases
 *     - `Livewire::component('<alias>', <Class>::class)` registrations;
 *     - the convention alias -> App\Livewire\<Studly> and <PSR-4 prefix>\
 *       Livewire\<Studly>;
 *     - the view `livewire.<alias>` when no class exists.
 *
 * Output shape: see the README of benchmarks/php. The top level holds `stats`,
 * `roots`, `classes`, `ambiguous_classes`, `views`, `files` (per file: language,
 * namespace, use map, declarations and references with the target file when one
 * was resolved) and `edges` (the resolved file-to-file dependencies). Ambiguity
 * is always reported and never guessed.
 */

const REFERENCE_SCHEMA = 1;

/**
 * The directories the graph builder does not discover, so that the reference
 * sees the same tree as the graph it is compared with. Kept in the same order as
 * benchmarks/php/tools/list_files.py.
 */
const SKIPPED_DIRECTORIES = [
    'vendor',
    'node_modules',
    'dist',
    '.git',
    '.next',
    '.turbo',
    'build',
    'coverage',
    '.tox',
    'site-packages',
    '.mypy_cache',
    '.pytest_cache',
    '.ruff_cache',
    'target',
    '.venv',
    'venv',
    '__pycache__',
    '.worktrees',
];

/** Names PHP treats as types or as the current class, never as another file. */
const NON_CLASS_NAMES = [
    'self',
    'static',
    'parent',
    'string',
    'int',
    'float',
    'bool',
    'array',
    'callable',
    'iterable',
    'object',
    'mixed',
    'void',
    'never',
    'null',
    'false',
    'true',
    'resource',
    'class',
    'function',
];

/** @return bool whether a token can be (part of) a class name */
function is_name_token(array $token): bool
{
    return in_array($token['id'], [T_STRING, T_NAME_QUALIFIED, T_NAME_FULLY_QUALIFIED, T_NAME_RELATIVE], true);
}

/** The last segment of a fully qualified name. */
function short_name(string $name): string
{
    $position = strrpos($name, '\\');

    return $position === false ? $name : substr($name, $position + 1);
}

/** kebab-case of a StudlyCase module directory: BillingReports -> billing-reports. */
function kebab_case(string $name): string
{
    $dashed = preg_replace('/(?<!^)[A-Z]/', '-$0', $name) ?? $name;

    return strtolower($dashed);
}

/** StudlyCase of a Livewire alias: user-profile -> UserProfile. */
function studly_case(string $name): string
{
    $words = str_replace(['-', '_'], ' ', $name);
    $words = str_replace(' ', '', ucwords($words));

    return $words;
}

/**
 * The conventional alias of a component class: Admin\\Panel -> admin.panel.
 */
function livewire_alias_from_class(string $tail): string
{
    return implode('.', array_map('kebab_case', explode('\\', $tail)));
}

/**
 * The class path of a Livewire alias: foo-bar -> FooBar, dir.name -> Dir\\Name.
 * A package alias (x::y) names no class of this checkout.
 */
function livewire_class_path(string $alias): ?string
{
    if (str_contains($alias, '::')) {
        return null;
    }
    $segments = [];
    foreach (explode('.', $alias) as $segment) {
        $segment = studly_case($segment);
        if ($segment === '') {
            return null;
        }
        $segments[] = $segment;
    }

    return implode('\\', $segments);
}

/**
 * Collapse "." and ".." segments. Keeps a leading slash of an absolute path.
 */
function normalize_path(string $path): string
{
    $path = str_replace('\\', '/', $path);
    $absolute = str_starts_with($path, '/');
    $out = [];
    foreach (explode('/', $path) as $segment) {
        if ($segment === '' || $segment === '.') {
            continue;
        }
        if ($segment === '..') {
            if ($out !== [] && end($out) !== '..') {
                array_pop($out);
            } elseif (!$absolute) {
                $out[] = '..';
            }
            continue;
        }
        $out[] = $segment;
    }

    return ($absolute ? '/' : '') . implode('/', $out);
}

/** A path relative to the root, or null when it is outside it. */
function relative_to(string $absolute, string $root): ?string
{
    $absolute = normalize_path($absolute);
    $root = rtrim(normalize_path($root), '/');
    if ($absolute === $root) {
        return '';
    }
    if (!str_starts_with($absolute, $root . '/')) {
        return null;
    }

    return substr($absolute, strlen($root) + 1);
}

/** The value of a single-quoted or a non-interpolated double-quoted literal. */
function unquote_literal(string $literal): ?string
{
    $literal = trim($literal);
    if (strlen($literal) < 2) {
        return null;
    }
    $quote = $literal[0];
    if (($quote !== "'" && $quote !== '"') || $literal[strlen($literal) - 1] !== $quote) {
        return null;
    }
    $body = substr($literal, 1, -1);
    if ($quote === '"' && str_contains($body, '$')) {
        return null;
    }
    if ($quote === '"') {
        return stripcslashes($body);
    }

    return str_replace(["\\'", '\\\\'], ["'", '\\'], $body);
}

/**
 * Split an argument list at its top-level commas.
 *
 * @return list<string>
 */
function split_arguments(string $inner): array
{
    $parts = [];
    $current = '';
    $quote = null;
    $round = 0;
    $square = 0;
    $length = strlen($inner);
    for ($i = 0; $i < $length; $i++) {
        $character = $inner[$i];
        if ($quote !== null) {
            $current .= $character;
            if ($character === '\\' && $i + 1 < $length) {
                $current .= $inner[++$i];
            } elseif ($character === $quote) {
                $quote = null;
            }
        } elseif ($character === "'" || $character === '"') {
            $quote = $character;
            $current .= $character;
        } elseif ($character === '(') {
            $round++;
            $current .= $character;
        } elseif ($character === ')') {
            $round--;
            $current .= $character;
        } elseif ($character === '[') {
            $square++;
            $current .= $character;
        } elseif ($character === ']') {
            $square--;
            $current .= $character;
        } elseif ($character === ',' && $round === 0 && $square === 0) {
            $parts[] = trim($current);
            $current = '';
        } else {
            $current .= $character;
        }
    }
    if (trim($current) !== '') {
        $parts[] = trim($current);
    }

    return $parts;
}

/**
 * Split a concatenation expression at its top-level dots.
 *
 * @return list<string>
 */
function split_concatenation(string $expression): array
{
    $parts = [];
    $current = '';
    $quote = null;
    $depth = 0;
    $length = strlen($expression);
    for ($i = 0; $i < $length; $i++) {
        $character = $expression[$i];
        if ($quote !== null) {
            $current .= $character;
            if ($character === '\\' && $i + 1 < $length) {
                $current .= $expression[++$i];
            } elseif ($character === $quote) {
                $quote = null;
            }
        } elseif ($character === "'" || $character === '"') {
            $quote = $character;
            $current .= $character;
        } elseif ($character === '(' || $character === '[') {
            $depth++;
            $current .= $character;
        } elseif ($character === ')' || $character === ']') {
            $depth--;
            $current .= $character;
        } elseif ($character === '.' && $depth === 0) {
            $parts[] = trim($current);
            $current = '';
        } else {
            $current .= $character;
        }
    }
    $parts[] = trim($current);

    return $parts;
}

/**
 * Evaluate the path expression of a view registration, e.g.
 * __DIR__.'/../../resources/views' or base_path('modules/Blog/resources/views').
 *
 * Returns the path relative to the checkout root, or null when the expression
 * cannot be evaluated statically.
 */
function evaluate_path_expression(string $expression, string $fileAbsolute, string $root): ?string
{
    $parts = split_concatenation(trim($expression));
    $result = '';
    foreach ($parts as $part) {
        $value = evaluate_path_term($part, $fileAbsolute, $root);
        if ($value === null) {
            return null;
        }
        $result .= $value;
    }
    $result = normalize_path($result);
    if (!str_starts_with($result, '/')) {
        return null;
    }

    return relative_to($result, $root);
}

/** One term of a concatenation: a literal, __DIR__, or a known path helper. */
function evaluate_path_term(string $term, string $fileAbsolute, string $root): ?string
{
    $term = trim($term);
    if ($term === '') {
        return null;
    }
    $literal = unquote_literal($term);
    if ($literal !== null) {
        return $literal;
    }
    switch ($term) {
        case '__DIR__':
            return dirname($fileAbsolute);
        case '__FILE__':
            return $fileAbsolute;
        case 'DIRECTORY_SEPARATOR':
            return '/';
    }
    if (!preg_match('/^([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)$/s', $term, $match)) {
        return null;
    }
    $function = strtolower($match[1]);
    $arguments = split_arguments($match[2]);
    $values = [];
    foreach ($arguments as $argument) {
        if (str_contains($argument, ':') && preg_match('/^[A-Za-z_][A-Za-z0-9_]*\s*:/', $argument)) {
            return null; // a named argument: not evaluated statically
        }
        if ($function === 'dirname' && count($values) === 1) {
            $values[] = $argument;
            continue;
        }
        $value = evaluate_path_expression($argument, $fileAbsolute, $root === '' ? '/' : $root);
        if ($value === null) {
            return null;
        }
        $values[] = $value;
    }
    $joined = implode('/', array_filter($values, static fn (string $value): bool => $value !== ''));
    switch ($function) {
        case 'base_path':
            return $root . ($joined === '' ? '' : '/' . $joined);
        case 'app_path':
            return $root . '/app' . ($joined === '' ? '' : '/' . $joined);
        case 'resource_path':
            return $root . '/resources' . ($joined === '' ? '' : '/' . $joined);
        case 'public_path':
            return $root . '/public' . ($joined === '' ? '' : '/' . $joined);
        case 'storage_path':
            return $root . '/storage' . ($joined === '' ? '' : '/' . $joined);
        case 'realpath':
            $path = realpath($root . '/' . $joined);

            return $path === false ? null : $path;
        case 'dirname':
            $levels = isset($values[1]) ? max(1, (int) $values[1]) : 1;
            $path = $values[0] ?? null;
            if ($path === null) {
                return null;
            }
            for ($level = 0; $level < $levels; $level++) {
                $path = dirname($path);
            }

            return $path;
        default:
            return null;
    }
}

/**
 * One parsed PHP file: namespace, use map, declarations and references.
 */
final class PhpFileParser
{
    /** @var list<array{id:int|string,text:string,line:int}> */
    private array $tokens = [];

    private int $count = 0;

    private int $depth = 0;

    private string $namespace = '';

    private string $firstNamespace = '';

    /** @var list<array{ns:string,depth:int,previous:string}> */
    private array $namespaceBlocks = [];

    /** @var list<int> bodies of the classes currently open (brace depth inside) */
    private array $classBodies = [];

    private int $pendingBodyDepth = -1;

    /** @var array<string,array<string,string>> namespace -> lower alias -> FQCN */
    private array $usesByNamespace = [];

    /** @var list<array{name:string,kind:string,line:int}> */
    private array $declarations = [];

    /** @var list<array{raw:string,kind:string,line:int,ns:string}> */
    private array $references = [];

    /** @var list<array{alias:string,class:string,ns:string,line:int}> */
    private array $componentRegistrations = [];

    /** @var list<array{namespace:string,expression:string,file:string,line:int}> */
    private array $viewRegistrations = [];

    public function __construct(string $code, private string $absolutePath)
    {
        $line = 1;
        foreach (token_get_all($code) as $token) {
            if (is_array($token)) {
                [$id, $text] = $token;
                $line = $token[2];
                if (in_array($id, [T_WHITESPACE, T_COMMENT, T_DOC_COMMENT, T_OPEN_TAG, T_CLOSE_TAG, T_INLINE_HTML], true)) {
                    $line += substr_count($text, "\n");
                    continue;
                }
                $this->tokens[] = ['id' => $id, 'text' => $text, 'line' => $line];
                $line += substr_count($text, "\n");
                continue;
            }
            $this->tokens[] = ['id' => $token, 'text' => $token, 'line' => $line];
        }
        $this->count = count($this->tokens);
    }

    /** @return array{namespace:string,uses:array<string,string>,declarations:list<array{name:string,kind:string,line:int}>,references:list<array{raw:string,kind:string,line:int,ns:string}>,component_registrations:list<array{alias:string,class:string,ns:string,line:int}>,view_registrations:list<array{namespace:string,expression:string,file:string,line:int}>} */
    public function parse(): array
    {
        $i = 0;
        while ($i < $this->count) {
            $token = $this->tokens[$i];
            $text = $token['text'];
            if ($text === '{') {
                $this->depth++;
                if ($this->pendingBodyDepth === $this->depth) {
                    $this->classBodies[] = $this->depth;
                    $this->pendingBodyDepth = -1;
                }
                $i++;
                continue;
            }
            if ($text === '}') {
                if ($this->classBodies !== [] && end($this->classBodies) === $this->depth) {
                    array_pop($this->classBodies);
                }
                if ($this->namespaceBlocks !== [] && end($this->namespaceBlocks)['depth'] === $this->depth) {
                    $block = array_pop($this->namespaceBlocks);
                    $this->namespace = $block['previous'];
                }
                $this->depth--;
                $i++;
                continue;
            }
            $id = $token['id'];
            if ($id === T_NAMESPACE) {
                $i = $this->parseNamespace($i);
                continue;
            }
            if ($id === T_USE) {
                $next = $this->tokens[$i + 1] ?? null;
                $inClass = $this->classBodies !== [] && $this->depth >= end($this->classBodies);
                if ($inClass) {
                    if ($next !== null && $next['text'] === '(') {
                        $i++; // closure capture `use ($x)`
                        continue;
                    }
                    $i = $this->parseTraitUse($i);
                } else {
                    $i = $this->parseImport($i);
                }
                continue;
            }
            if ($id === T_CLASS || $id === T_INTERFACE || $id === T_TRAIT || $id === T_ENUM) {
                $i = $this->parseDeclaration($i);
                continue;
            }
            if ($id === T_NEW) {
                $this->parseNew($i);
                $i++;
                continue;
            }
            if ($id === T_INSTANCEOF) {
                $next = $this->tokens[$i + 1] ?? null;
                if ($next !== null && is_name_token($next)) {
                    $this->addReference($next['text'], 'instanceof', $next['line']);
                }
                $i++;
                continue;
            }
            if ($id === T_CATCH) {
                $this->parseCatch($i);
                $i++;
                continue;
            }
            if ($id === T_ATTRIBUTE) {
                $this->parseAttribute($i);
                $i++;
                continue;
            }
            if ($id === T_DOUBLE_COLON) {
                $this->parseDoubleColon($i);
                $i++;
                continue;
            }
            if ($id === T_FUNCTION || $id === T_FN) {
                $this->parseReturnType($i);
                $i++;
                continue;
            }
            if ($id === T_VARIABLE) {
                $this->parseVariableType($i);
                $i++;
                continue;
            }
            if ($id === T_STRING) {
                $this->parseStringToken($i);
                $i++;
                continue;
            }
            $i++;
        }

        return [
            'namespace' => $this->firstNamespace,
            'uses' => $this->usesByNamespace[$this->firstNamespace] ?? ($this->usesByNamespace[''] ?? []),
            'declarations' => $this->declarations,
            'references' => $this->references,
            'component_registrations' => $this->componentRegistrations,
            'view_registrations' => $this->viewRegistrations,
        ];
    }

    private function parseNamespace(int $i): int
    {
        $j = $i + 1;
        $name = '';
        if ($j < $this->count && is_name_token($this->tokens[$j])) {
            $name = $this->tokens[$j]['text'];
            $j++;
        }
        if ($this->firstNamespace === '' && $name !== '') {
            $this->firstNamespace = $name;
        }
        if ($j < $this->count && $this->tokens[$j]['text'] === ';') {
            $this->namespace = $name;

            return $j;
        }
        if ($j < $this->count && $this->tokens[$j]['text'] === '{') {
            $this->namespaceBlocks[] = ['ns' => $name, 'depth' => $this->depth + 1, 'previous' => $this->namespace];
            $this->namespace = $name;

            return $j;
        }

        return $i + 1;
    }

    /**
     * A `use` statement at namespace level: import a class (or a group of them).
     */
    private function parseImport(int $i): int
    {
        $line = $this->tokens[$i]['line'];
        $j = $i + 1;
        if ($j >= $this->count) {
            return $i + 1;
        }
        if ($this->tokens[$j]['id'] === T_FUNCTION || $this->tokens[$j]['id'] === T_CONST) {
            return $this->skipStatement($j);
        }
        if ($this->tokens[$j]['text'] === '(') {
            return $i + 1;
        }
        [$name, $j] = $this->readName($j);
        if ($name === null) {
            return $i + 1;
        }
        $name = ltrim($name, '\\');
        if ($j < $this->count && $this->tokens[$j]['text'] === '{') {
            $j++;
            while ($j < $this->count && $this->tokens[$j]['text'] !== '}') {
                if ($this->tokens[$j]['text'] === ',') {
                    $j++;
                    continue;
                }
                [$part, $next] = $this->readName($j);
                if ($part === null) {
                    $j++;
                    continue;
                }
                $j = $next;
                $alias = short_name($part);
                if ($j < $this->count && $this->tokens[$j]['id'] === T_AS) {
                    $j++;
                    if ($j < $this->count && is_name_token($this->tokens[$j])) {
                        $alias = $this->tokens[$j]['text'];
                        $j++;
                    }
                }
                $this->recordUse(rtrim($name, '\\') . '\\' . ltrim($part, '\\'), $alias, $line);
            }

            return $j + 1;
        }
        while (true) {
            $alias = short_name($name);
            if ($j < $this->count && $this->tokens[$j]['id'] === T_AS) {
                $j++;
                if ($j < $this->count && is_name_token($this->tokens[$j])) {
                    $alias = $this->tokens[$j]['text'];
                    $j++;
                }
            }
            $this->recordUse($name, $alias, $line);
            if ($j >= $this->count || $this->tokens[$j]['text'] !== ',') {
                break;
            }
            $j++;
            [$name, $j] = $this->readName($j);
            if ($name === null) {
                break;
            }
            $name = ltrim($name, '\\');
        }

        return $j;
    }

    private function recordUse(string $fqcn, string $alias, int $line): void
    {
        $fqcn = ltrim($fqcn, '\\');
        if ($fqcn === '') {
            return;
        }
        $this->usesByNamespace[$this->namespace][strtolower($alias)] = $fqcn;
        $this->addReference($fqcn, 'use', $line);
    }

    /**
     * A trait `use` inside a class body, with its optional adaptation block.
     */
    private function parseTraitUse(int $i): int
    {
        $j = $i + 1;
        while ($j < $this->count) {
            $token = $this->tokens[$j];
            if ($token['text'] === ';') {
                return $j;
            }
            if ($token['text'] === '{') {
                $j++;
                while ($j < $this->count && $this->tokens[$j]['text'] !== '}') {
                    $current = $this->tokens[$j];
                    if (is_name_token($current)
                        && ($this->tokens[$j + 1]['text'] ?? '') === '::') {
                        $this->addReference($current['text'], 'trait', $current['line']);
                    }
                    if ($current['id'] === T_INSTEADOF) {
                        $j++;
                        while ($j < $this->count && is_name_token($this->tokens[$j])) {
                            $this->addReference($this->tokens[$j]['text'], 'trait', $this->tokens[$j]['line']);
                            $j++;
                            if (($this->tokens[$j]['text'] ?? '') === ',') {
                                $j++;
                                continue;
                            }
                            break;
                        }
                        continue;
                    }
                    $j++;
                }

                return $j + 1;
            }
            if (is_name_token($token)) {
                [$name, $next] = $this->readName($j);
                if ($name !== null) {
                    $this->addReference($name, 'trait', $token['line']);
                    $j = $next;
                    continue;
                }
            }
            $j++;
        }

        return $j;
    }

    /**
     * A named or anonymous class-like declaration.
     */
    private function parseDeclaration(int $i): int
    {
        $token = $this->tokens[$i];
        $previous = $i > 0 ? $this->tokens[$i - 1] : null;
        if ($token['id'] === T_CLASS) {
            $isNew = $previous !== null && $previous['id'] === T_NEW;
            $isNewReadonly = $previous !== null && $previous['id'] === T_READONLY
                && $i > 1 && $this->tokens[$i - 2]['id'] === T_NEW;
            if ($isNew || $isNewReadonly) {
                return $this->parseAnonymousClassTail($i);
            }
            if ($previous !== null && $previous['text'] === '::') {
                return $i + 1; // X::class
            }
        }
        $kind = match ($token['id']) {
            T_INTERFACE => 'interface',
            T_TRAIT => 'trait',
            T_ENUM => 'enum',
            default => 'class',
        };
        $nameToken = $this->tokens[$i + 1] ?? null;
        if ($nameToken === null || !is_name_token($nameToken)) {
            return $i + 1;
        }
        $name = $nameToken['text'];
        $fqcn = $this->namespace === '' ? $name : $this->namespace . '\\' . $name;
        $this->declarations[] = ['name' => $fqcn, 'kind' => $kind, 'line' => $nameToken['line']];
        $j = $i + 2;
        $mode = null;
        while ($j < $this->count) {
            $current = $this->tokens[$j];
            if ($current['text'] === '{') {
                $this->pendingBodyDepth = $this->depth + 1;

                return $j;
            }
            if ($current['text'] === ';') {
                return $j;
            }
            if ($current['id'] === T_EXTENDS) {
                $mode = 'extends';
                $j++;
                continue;
            }
            if ($current['id'] === T_IMPLEMENTS) {
                $mode = 'implements';
                $j++;
                continue;
            }
            if ($current['text'] === '(') {
                $j = $this->skipBalanced($j, '(', ')');
                continue;
            }
            if (is_name_token($current)) {
                if ($mode !== null) {
                    $this->addReference($current['text'], $mode, $current['line']);
                }
                $j++;
                continue;
            }
            $j++;
        }

        return $j;
    }

    /**
     * The body of an anonymous class: `new class($args) extends A implements B {`.
     */
    private function parseAnonymousClassTail(int $i): int
    {
        $j = $i + 1;
        $mode = null;
        while ($j < $this->count) {
            $current = $this->tokens[$j];
            if ($current['text'] === '{') {
                $this->pendingBodyDepth = $this->depth + 1;

                return $j;
            }
            if ($current['text'] === ';') {
                return $j;
            }
            if ($current['id'] === T_EXTENDS) {
                $mode = 'extends';
                $j++;
                continue;
            }
            if ($current['id'] === T_IMPLEMENTS) {
                $mode = 'implements';
                $j++;
                continue;
            }
            if ($current['text'] === '(') {
                $j = $this->skipBalanced($j, '(', ')');
                continue;
            }
            if (is_name_token($current) && $mode !== null) {
                $this->addReference($current['text'], $mode, $current['line']);
            }
            $j++;
        }

        return $j;
    }

    private function parseNew(int $i): void
    {
        $j = $i + 1;
        while ($j < $this->count && $this->tokens[$j]['id'] === T_READONLY) {
            $j++;
        }
        $next = $this->tokens[$j] ?? null;
        if ($next !== null && is_name_token($next)) {
            $this->addReference($next['text'], 'new', $next['line']);
        }
    }

    private function parseCatch(int $i): void
    {
        $j = $i + 1;
        if (($this->tokens[$j]['text'] ?? '') !== '(') {
            return;
        }
        $j++;
        while ($j < $this->count && $this->tokens[$j]['text'] !== ')') {
            $current = $this->tokens[$j];
            if (is_name_token($current)) {
                $this->addReference($current['text'], 'catch', $current['line']);
            }
            $j++;
        }
    }

    private function parseAttribute(int $i): void
    {
        $j = $i + 1;
        $depth = 1;
        $round = 0;
        $expectName = true;
        while ($j < $this->count) {
            $current = $this->tokens[$j];
            $text = $current['text'];
            if ($text === '[') {
                $depth++;
            } elseif ($text === ']') {
                $depth--;
                if ($depth === 0) {
                    return;
                }
            } elseif ($text === '(') {
                $round++;
            } elseif ($text === ')') {
                $round--;
            } elseif ($depth === 1 && $round === 0) {
                if ($text === ',') {
                    $expectName = true;
                } elseif ($expectName && is_name_token($current)) {
                    $this->addReference($text, 'attribute', $current['line']);
                    $expectName = false;
                }
            }
            $j++;
        }
    }

    private function parseDoubleColon(int $i): void
    {
        $parts = [];
        $j = $i - 1;
        while ($j >= 0) {
            $current = $this->tokens[$j];
            if (is_name_token($current) || $current['id'] === T_NS_SEPARATOR) {
                array_unshift($parts, $current['text']);
                $j--;
                continue;
            }
            break;
        }
        if ($parts === []) {
            return;
        }
        if ($j >= 0) {
            $before = $this->tokens[$j];
            if ($before['id'] === T_OBJECT_OPERATOR || $before['id'] === T_NULLSAFE_OBJECT_OPERATOR) {
                return; // $object->property::member, not a class name
            }
        }
        $raw = implode('', $parts);
        if ($raw === '') {
            return;
        }
        $this->addReference($raw, 'static', $this->tokens[$i - 1]['line']);

        $following = $this->tokens[$i + 1] ?? null;
        if ($following === null || !is_name_token($following)) {
            return;
        }
        $member = strtolower($following['text']);
        $target = strtolower(short_name($raw));
        $open = $i + 2;
        if (($this->tokens[$open]['text'] ?? '') !== '(') {
            return;
        }
        if ($target === 'livewire' && $member === 'component') {
            $arguments = $this->callArguments($open);
            if ($arguments !== null && count($arguments) >= 2) {
                $alias = unquote_literal($arguments[0]);
                $class = $this->classExpression($arguments[1]);
                if ($alias !== null && $class !== null) {
                    $this->componentRegistrations[] = [
                        'alias' => $alias,
                        'class' => $class,
                        'ns' => $this->namespace,
                        'uses' => $this->usesByNamespace[$this->namespace] ?? [],
                        'line' => $following['line'],
                    ];
                }
            }
        } elseif ($target === 'view' && $member === 'addnamespace') {
            $arguments = $this->callArguments($open);
            if ($arguments !== null && count($arguments) >= 2) {
                $namespace = unquote_literal($arguments[0]);
                if ($namespace !== null) {
                    $this->viewRegistrations[] = [
                        'namespace' => $namespace,
                        'expression' => $arguments[1],
                        'file' => $this->absolutePath,
                        'line' => $following['line'],
                    ];
                }
            }
        } elseif ($target === 'view' && $member === 'make') {
            $literal = $this->firstLiteralArgument($open);
            if ($literal !== null) {
                $this->addReference($literal, 'view', $following['line']);
            }
        }
    }

    private function parseReturnType(int $i): void
    {
        $j = $i + 1;
        while ($j < $this->count && $this->tokens[$j]['text'] !== '(') {
            if ($this->tokens[$j]['text'] === '{' || $this->tokens[$j]['text'] === ';') {
                return;
            }
            $j++;
        }
        if ($j >= $this->count) {
            return;
        }
        $j = $this->skipBalanced($j, '(', ')');
        $j++;
        if (($this->tokens[$j]['id'] ?? null) === T_USE) {
            $j++;
            if (($this->tokens[$j]['text'] ?? '') === '(') {
                $j = $this->skipBalanced($j, '(', ')');
                $j++;
            }
        }
        if (($this->tokens[$j]['text'] ?? '') !== ':') {
            return;
        }
        $j++;
        while ($j < $this->count) {
            $current = $this->tokens[$j];
            if (in_array($current['text'], ['{', ';', '=>'], true)) {
                return;
            }
            if (is_name_token($current)) {
                $this->addReference($current['text'], 'return', $current['line']);
            }
            $j++;
        }
    }

    /**
     * The type of a parameter, property or promoted property: the names directly
     * before its variable. `(` ends the scan, so a function name is never taken
     * for a type.
     */
    private function parseVariableType(int $i): void
    {
        $parts = [];
        $j = $i - 1;
        while ($j >= 0) {
            $current = $this->tokens[$j];
            if (is_name_token($current) || $current['id'] === T_NS_SEPARATOR) {
                array_unshift($parts, $current['text']);
                $j--;
                continue;
            }
            if (in_array($current['text'], ['?', '|', '&', ')'], true)
                || $current['id'] === T_ELLIPSIS) {
                $j--;
                continue;
            }
            break;
        }
        if ($j >= 0) {
            $before = $this->tokens[$j];
            if ($before['id'] === T_OBJECT_OPERATOR || $before['id'] === T_NULLSAFE_OBJECT_OPERATOR
                || $before['id'] === T_DOUBLE_COLON) {
                return; // $object->property, not a type hint
            }
        }
        $raw = implode('', $parts);
        if ($raw === '') {
            return;
        }
        $this->addReference($raw, 'type', $this->tokens[$i]['line']);
    }

    private function parseStringToken(int $i): void
    {
        $token = $this->tokens[$i];
        $lower = strtolower($token['text']);
        $next = $this->tokens[$i + 1] ?? null;
        if ($lower === 'loadviewsfrom' && $next !== null && $next['text'] === '(') {
            $arguments = $this->callArguments($i + 1);
            if ($arguments !== null && count($arguments) >= 2) {
                $namespace = unquote_literal($arguments[1]);
                if ($namespace !== null) {
                    $this->viewRegistrations[] = [
                        'namespace' => $namespace,
                        'expression' => $arguments[0],
                        'file' => $this->absolutePath,
                        'line' => $token['line'],
                    ];
                }
            }

            return;
        }
        if ($lower === 'view' && $next !== null && $next['text'] === '(') {
            $previous = $i > 0 ? $this->tokens[$i - 1] : null;
            if ($previous !== null && ($previous['id'] === T_FUNCTION || $previous['id'] === T_FN)) {
                return; // a method or function named view
            }
            if ($previous !== null && $previous['text'] === '&' && $i > 1 && $this->tokens[$i - 2]['id'] === T_FUNCTION) {
                return;
            }
            if ($previous !== null && $previous['id'] === T_DOUBLE_COLON) {
                $class = $this->staticClassName($i - 1);
                $class = strtolower(short_name(ltrim($class, '\\')));
                if ($class === 'route') {
                    $literal = $this->literalArgument($i + 1, 1);
                } elseif ($class === 'view') {
                    $literal = $this->literalArgument($i + 1, 0);
                } else {
                    $literal = null;
                }
            } else {
                $literal = $this->firstLiteralArgument($i + 1);
            }
            if ($literal !== null) {
                $this->addReference($literal, 'view', $token['line']);
            }

            return;
        }
        // view()->make('a.b')
        if ($lower === 'make' && $next !== null && $next['text'] === '(' && $i >= 4) {
            if ($this->tokens[$i - 1]['id'] === T_OBJECT_OPERATOR
                && $this->tokens[$i - 2]['text'] === ')'
                && $this->tokens[$i - 3]['text'] === '('
                && strtolower($this->tokens[$i - 4]['text']) === 'view') {
                $literal = $this->firstLiteralArgument($i + 1);
                if ($literal !== null) {
                    $this->addReference($literal, 'view', $token['line']);
                }
            }
        }
    }

    private function addReference(string $raw, string $kind, int $line): void
    {
        $raw = trim($raw);
        if ($raw === '') {
            return;
        }
        if ($kind !== 'use' && in_array(strtolower(ltrim($raw, '\\')), NON_CLASS_NAMES, true)) {
            return;
        }
        $this->references[] = [
            'raw' => $raw,
            'kind' => $kind,
            'line' => $line,
            'ns' => $this->namespace,
            'uses' => $this->usesByNamespace[$this->namespace] ?? [],
        ];
    }

    /**
     * @return array{0:?string,1:int}
     */
    private function readName(int $j): array
    {
        $text = '';
        $started = false;
        while ($j < $this->count) {
            $current = $this->tokens[$j];
            if (is_name_token($current)) {
                $text .= $current['text'];
                $started = true;
                $j++;
                continue;
            }
            if ($current['id'] === T_NS_SEPARATOR) {
                $text .= '\\';
                $started = true;
                $j++;
                continue;
            }
            break;
        }

        return $started ? [$text, $j] : [null, $j];
    }

    /** Index of the closing token of a balanced pair, starting at $open. */
    private function skipBalanced(int $open, string $opening, string $closing): int
    {
        $depth = 0;
        for ($j = $open; $j < $this->count; $j++) {
            $text = $this->tokens[$j]['text'];
            if ($text === $opening) {
                $depth++;
            } elseif ($text === $closing) {
                $depth--;
                if ($depth === 0) {
                    return $j;
                }
            }
        }

        return $this->count - 1;
    }

    private function skipStatement(int $j): int
    {
        while ($j < $this->count) {
            if ($this->tokens[$j]['text'] === ';') {
                return $j;
            }
            $j++;
        }

        return $j;
    }

    /**
     * The arguments of a call, as source text, from its opening parenthesis.
     *
     * @return list<string>|null
     */
    private function callArguments(int $open): ?array
    {
        if (($this->tokens[$open]['text'] ?? '') !== '(') {
            return null;
        }
        $arguments = [];
        $current = '';
        $round = 0;
        $square = 0;
        for ($j = $open; $j < $this->count; $j++) {
            $text = $this->tokens[$j]['text'];
            if ($text === '(') {
                $round++;
                if ($round === 1) {
                    continue;
                }
                $current .= '(';
                continue;
            }
            if ($text === ')') {
                $round--;
                if ($round === 0) {
                    if (trim($current) !== '' || $arguments !== []) {
                        $arguments[] = trim($current);
                    }

                    return $arguments;
                }
                $current .= ')';
                continue;
            }
            if ($text === '[') {
                $square++;
            } elseif ($text === ']') {
                $square--;
            } elseif ($text === ',' && $round === 1 && $square === 0) {
                $arguments[] = trim($current);
                $current = '';
                continue;
            }
            $current .= $text . ' ';
        }

        return null;
    }

    private function firstLiteralArgument(int $open): ?string
    {
        return $this->literalArgument($open, 0);
    }

    private function literalArgument(int $open, int $index): ?string
    {
        $arguments = $this->callArguments($open);
        if ($arguments === null || !isset($arguments[$index])) {
            return null;
        }

        return unquote_literal($arguments[$index]);
    }

    /** The class name written before a T_DOUBLE_COLON at $index. */
    private function staticClassName(int $index): string
    {
        $parts = [];
        for ($j = $index - 1; $j >= 0; $j--) {
            $current = $this->tokens[$j];
            if (is_name_token($current) || $current['id'] === T_NS_SEPARATOR) {
                array_unshift($parts, $current['text']);
                continue;
            }
            break;
        }

        return implode('', $parts);
    }

    /** `Counter::class` -> `Counter`; anything else (a variable, a new call) -> null. */
    private function classExpression(string $expression): ?string
    {
        $expression = trim($expression);
        if (!preg_match('/^(.+?)\s*::\s*class$/i', $expression, $match)) {
            return null;
        }

        return trim($match[1]);
    }
}

/**
 * The references of one Blade template, from its text.
 */
final class BladeScanner
{
    public function __construct(private string $content)
    {
    }

    /** @return list<array{raw:string,kind:string,line:int}> */
    public function scan(): array
    {
        $content = preg_replace_callback(
            '/\{\{--.*?--\}\}/s',
            static fn (array $match): string => str_repeat("\n", substr_count($match[0], "\n")),
            $this->content
        ) ?? $this->content;
        $references = [];
        $directivePattern = '/@(extends|includeFirst|includeIf|includeWhen|includeUnless|include|component|each|livewire)\s*\(/';
        preg_match_all($directivePattern, $content, $matches, PREG_OFFSET_CAPTURE);
        foreach ($matches[0] as $index => $match) {
            $name = $matches[1][$index][0];
            $offset = $match[1];
            $open = $offset + strlen($match[0]) - 1;
            $inner = $this->callInner($content, $open);
            if ($inner === null) {
                continue;
            }
            $line = substr_count(substr($content, 0, $offset), "\n") + 1;
            $kind = match ($name) {
                'extends' => 'extends',
                'component', 'each' => 'component',
                'livewire' => 'livewire',
                default => 'include',
            };
            $literals = $this->stringLiterals($inner);
            if ($name === 'includeFirst') {
                foreach ($literals as $literal) {
                    $references[] = ['raw' => $literal, 'kind' => 'include', 'line' => $line];
                }
                continue;
            }
            if ($literals !== []) {
                $references[] = ['raw' => $literals[0], 'kind' => $kind, 'line' => $line];
            }
        }

        preg_match_all('/<x-([A-Za-z0-9_.\-]+(?:::[A-Za-z0-9_.\-]+)?)/', $content, $tags, PREG_OFFSET_CAPTURE);
        foreach ($tags[0] as $index => $tag) {
            $body = $tags[1][$index][0];
            $offset = $tag[1];
            $line = substr_count(substr($content, 0, $offset), "\n") + 1;
            if (str_starts_with($body, 'slot')) {
                continue;
            }
            if ($body === 'dynamic-component') {
                $end = strpos($content, '>', $offset);
                $fragment = $end === false ? '' : substr($content, $offset, $end - $offset);
                if (preg_match('/:component\s*=\s*(["\'])(.*?)\1/s', $fragment, $attribute)) {
                    $literal = unquote_literal($attribute[2]);
                    if ($literal === null && preg_match('/^([\'"])(.*?)\1$/', $attribute[2], $quoted)) {
                        $literal = $quoted[2];
                    }
                    if ($literal === null) {
                        $literal = unquote_literal(trim($attribute[2]));
                    }
                    if ($literal !== null) {
                        $references[] = ['raw' => $literal, 'kind' => 'component', 'line' => $line];
                    }
                }
                continue;
            }
            if (str_contains($body, '::')) {
                [$namespace, $name] = explode('::', $body, 2);
                $references[] = ['raw' => $namespace . '::components.' . $name, 'kind' => 'component', 'line' => $line];
                continue;
            }
            $references[] = ['raw' => 'components.' . $body, 'kind' => 'component', 'line' => $line];
        }

        preg_match_all('/<livewire:([A-Za-z0-9_.\-]+)(::[A-Za-z0-9_.\-]+)?/', $content, $components, PREG_OFFSET_CAPTURE);
        foreach ($components[0] as $index => $tag) {
            $alias = $components[1][$index][0] . ($components[2][$index][0] ?? '');
            $offset = $tag[1];
            $line = substr_count(substr($content, 0, $offset), "\n") + 1;
            $references[] = ['raw' => $alias, 'kind' => 'livewire', 'line' => $line];
        }

        return $references;
    }

    private function callInner(string $content, int $open): ?string
    {
        if (($content[$open] ?? '') !== '(') {
            return null;
        }
        $depth = 0;
        $quote = null;
        $length = strlen($content);
        for ($i = $open; $i < $length; $i++) {
            $character = $content[$i];
            if ($quote !== null) {
                if ($character === '\\') {
                    $i++;
                } elseif ($character === $quote) {
                    $quote = null;
                }
                continue;
            }
            if ($character === "'" || $character === '"') {
                $quote = $character;
                continue;
            }
            if ($character === '(') {
                $depth++;
                continue;
            }
            if ($character === ')') {
                $depth--;
                if ($depth === 0) {
                    return substr($content, $open + 1, $i - $open - 1);
                }
            }
        }

        return null;
    }

    /** @return list<string> */
    private function stringLiterals(string $inner): array
    {
        preg_match_all('/([\'"])((?:\\\\.|(?!\1).)*)\1/s', $inner, $matches, PREG_SET_ORDER);
        $literals = [];
        foreach ($matches as $match) {
            if ($match[1] === '"' && str_contains($match[2], '$')) {
                continue;
            }
            $literals[] = stripcslashes($match[2]);
        }

        return $literals;
    }
}

/**
 * Resolve a class name written in a file to a fully qualified name.
 *
 * @param array<string,string> $uses lower alias -> FQCN
 */
function resolve_reference_name(string $raw, string $namespace, array $uses): ?string
{
    $raw = trim($raw);
    if ($raw === '') {
        return null;
    }
    $lower = strtolower($raw);
    if (in_array($lower, NON_CLASS_NAMES, true)) {
        return null;
    }
    if ($raw[0] === '\\') {
        return ltrim($raw, '\\');
    }
    if (str_starts_with($lower, 'namespace\\')) {
        $rest = substr($raw, strlen('namespace\\'));

        return $namespace === '' ? $rest : $namespace . '\\' . $rest;
    }
    if (str_contains($raw, '\\')) {
        [$first, $rest] = explode('\\', $raw, 2);
        $alias = strtolower($first);
        if (isset($uses[$alias])) {
            return $uses[$alias] . '\\' . $rest;
        }

        return $namespace === '' ? $raw : $namespace . '\\' . $raw;
    }
    if (isset($uses[$lower])) {
        return $uses[$lower];
    }

    return $namespace === '' ? $raw : $namespace . '\\' . $raw;
}

/**
 * The file that declares a class, through the declaration index then PSR-4.
 *
 * @param array<string,list<string>> $classIndex lower FQCN -> files
 * @param list<array{0:string,1:string}> $psr4 prefix, directory relative to the root
 * @param array<string,bool> $fileSet
 * @return array{0:?string,1:string,2:list<string>} target, status, candidates
 */
function psr4_files(string $fqcn, array $psr4, array $fileSet): array
{
    $best = -1;
    $files = [];
    foreach ($psr4 as [$prefix, $directory]) {
        if ($prefix !== '' && !str_starts_with($fqcn, $prefix)) {
            continue;
        }
        $length = strlen($prefix);
        if ($length < $best) {
            continue;
        }
        $tail = substr($fqcn, $length);
        if ($tail === '' || str_starts_with($tail, '\\')) {
            continue;
        }
        $candidate = normalize_path(
            ($directory === '' ? '' : $directory . '/') . str_replace('\\', '/', $tail) . '.php'
        );
        if ($length > $best) {
            $best = $length;
            $files = [];
        }
        if (isset($fileSet[$candidate])) {
            $files[$candidate] = true;
        }
    }

    return array_keys($files);
}

function class_target(string $fqcn, array $classIndex, array $psr4, array $fileSet): array
{
    $fqcn = ltrim($fqcn, '\\');
    if ($fqcn === '') {
        return [null, 'unresolved', []];
    }
    $declared = $classIndex[strtolower($fqcn)] ?? [];
    $declared = array_values(array_unique($declared));
    if (count($declared) === 1) {
        return [$declared[0], 'resolved', $declared];
    }
    $psr4Candidates = psr4_files($fqcn, $psr4, $fileSet);
    if (count($declared) > 1) {
        // A generated IDE-helper file declares stub classes for every model. The
        // file the PSR-4 map names is the real declaration and wins; without one
        // the name stays ambiguous and resolves to nothing.
        $preferred = array_values(array_intersect($declared, $psr4Candidates));
        if (count($preferred) === 1) {
            return [$preferred[0], 'resolved', $preferred];
        }
        if (count($preferred) > 1) {
            return [null, 'ambiguous', $preferred];
        }

        return [null, 'ambiguous', $declared];
    }
    if (count($psr4Candidates) === 1) {
        return [$psr4Candidates[0], 'resolved', $psr4Candidates];
    }
    if (count($psr4Candidates) > 1) {
        return [null, 'ambiguous', $psr4Candidates];
    }

    return [null, 'unresolved', []];
}

/**
 * Resolve a Blade view name to its file.
 *
 * @param array<string,list<string>> $namespaceRoots
 * @param list<string> $defaultRoots
 * @param array<string,bool> $fileSet
 * @return array{0:?string,1:string,2:list<string>}
 */
function view_target(string $name, ?string $namespace, array $namespaceRoots, array $defaultRoots, array $fileSet): array
{
    $name = trim($name);
    if ($name === '') {
        return [null, 'unresolved', []];
    }
    $name = preg_replace('/\.blade\.php$/i', '', $name) ?? $name;
    $name = preg_replace('/\.php$/i', '', $name) ?? $name;
    $namespace ??= '';
    if ($namespace === '' && str_contains($name, '::')) {
        [$namespace, $name] = explode('::', $name, 2);
    }
    $path = trim(str_replace('.', '/', $name), '/');
    if ($path === '') {
        return [null, 'unresolved', []];
    }
    if ($namespace !== '') {
        $roots = $namespaceRoots[$namespace] ?? [];
        if ($roots === []) {
            // An unregistered namespace (the published `errors` views are the
            // usual case) is the directory of that name under a default root.
            $roots = $defaultRoots;
            $path = $namespace . '/' . $path;
        }
    } else {
        $roots = $defaultRoots;
    }
    $hits = [];
    foreach ($roots as $root) {
        foreach ([$path . '.blade.php', $path . '.php', $path . '/index.blade.php', $path . '/index.php'] as $candidate) {
            $relative = $root === '' ? $candidate : $root . '/' . $candidate;
            if (isset($fileSet[$relative])) {
                $hits[$relative] = true;
                break;
            }
        }
    }
    $hits = array_keys($hits);
    if (count($hits) === 1) {
        return [$hits[0], 'resolved', $hits];
    }
    if (count($hits) > 1) {
        return [null, 'ambiguous', $hits];
    }

    return [null, 'unresolved', []];
}

/**
 * Resolve a Livewire alias to its class file, or to its view when no class exists.
 *
 * @param array<string,list<?string>> $registrations lower alias -> class files
 * @param list<array{0:string,1:string}> $psr4
 * @param array<string,list<string>> $classIndex
 * @param array<string,bool> $fileSet
 * @param array<string,list<string>> $namespaceRoots
 * @param list<string> $defaultRoots
 * @return array{0:?string,1:string,2:list<string>}
 */
function livewire_target(
    string $alias,
    array $registrations,
    array $psr4,
    array $classIndex,
    array $fileSet,
    array $namespaceRoots,
    array $defaultRoots
): array {
    if (str_contains($alias, '::')) {
        return [null, 'unresolved', []];
    }
    $key = strtolower($alias);
    if (isset($registrations[$key])) {
        $targets = array_values(array_unique(array_filter($registrations[$key], static fn (?string $target): bool => $target !== null)));
        if (count($targets) === 1) {
            return [$targets[0], 'resolved', $targets];
        }
        if (count($targets) > 1) {
            return [null, 'ambiguous', $targets];
        }
    }
    $classPath = livewire_class_path($alias);
    if ($classPath === null) {
        return [null, 'unresolved', []];
    }
    $candidates = ['App\\Livewire\\' . $classPath];
    foreach ($psr4 as [$prefix, $directory]) {
        if ($prefix === '') {
            continue;
        }
        $candidates[] = rtrim($prefix, '\\') . '\\Livewire\\' . $classPath;
    }
    $targets = [];
    foreach (array_unique($candidates) as $fqcn) {
        [$target] = class_target($fqcn, $classIndex, $psr4, $fileSet);
        if ($target !== null) {
            $targets[$target] = true;
        }
    }
    $targets = array_keys($targets);
    if (count($targets) === 1) {
        return [$targets[0], 'resolved', $targets];
    }
    if (count($targets) > 1) {
        return [null, 'ambiguous', $targets];
    }

    return view_target('livewire.' . $alias, '', $namespaceRoots, $defaultRoots, $fileSet);
}

/**
 * Every composer.json of the checkout, as a PSR-4 prefix map.
 *
 * @return list<array{0:string,1:string}> prefix, directory relative to the root
 */
function discover_psr4(string $root, array $fileSet): array
{
    $composerFiles = [];
    $stack = [''];
    while ($stack !== []) {
        $relative = array_pop($stack);
        $directory = $relative === '' ? $root : $root . '/' . $relative;
        $entries = @scandir($directory);
        if ($entries === false) {
            continue;
        }
        foreach ($entries as $entry) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $absolute = $directory . '/' . $entry;
            if (is_link($absolute)) {
                continue;
            }
            if (is_dir($absolute)) {
                if (in_array($entry, SKIPPED_DIRECTORIES, true)
                    && !($entry === 'vendor' && str_ends_with($directory, '/resources/views'))) {
                    continue;
                }
                $stack[] = $relative === '' ? $entry : $relative . '/' . $entry;
                continue;
            }
            if ($entry === 'composer.json') {
                $composerFiles[] = $relative === '' ? 'composer.json' : $relative . '/composer.json';
            }
        }
    }
    sort($composerFiles);
    $map = [];
    foreach ($composerFiles as $composerFile) {
        $content = @file_get_contents($root . '/' . $composerFile);
        if ($content === false) {
            continue;
        }
        $data = json_decode($content, true);
        if (!is_array($data)) {
            fwrite(STDERR, "[php-reference] cannot read $composerFile\n");
            continue;
        }
        $base = dirname($composerFile);
        $base = $base === '.' ? '' : $base;
        foreach (['autoload', 'autoload-dev'] as $section) {
            $psr4 = $data[$section]['psr-4'] ?? null;
            if (!is_array($psr4)) {
                continue;
            }
            foreach ($psr4 as $prefix => $directories) {
                foreach ((array) $directories as $directory) {
                    if (!is_string($directory)) {
                        continue;
                    }
                    $relative = normalize_path(($base === '' ? '' : $base . '/') . $directory);
                    if ($relative === '..' || str_starts_with($relative, '../')) {
                        continue;
                    }
                    $map[] = [(string) $prefix, $relative];
                }
            }
        }
    }

    return $map;
}

/**
 * Run the resolver.
 *
 * @param list<string> $arguments
 */
function main(array $arguments): int
{
    if (count($arguments) < 2) {
        fwrite(STDERR, "usage: php php_reference.php <files.json> <out.json> [--root DIR]\n");

        return 2;
    }
    $filesJson = $arguments[0];
    $outJson = $arguments[1];
    $root = null;
    for ($i = 2; $i < count($arguments); $i++) {
        if ($arguments[$i] === '--root' && isset($arguments[$i + 1])) {
            $root = $arguments[++$i];
        }
    }
    $raw = @file_get_contents($filesJson);
    if ($raw === false) {
        fwrite(STDERR, "[php-reference] cannot read $filesJson\n");

        return 2;
    }
    $listed = json_decode($raw, true);
    if (!is_array($listed)) {
        fwrite(STDERR, "[php-reference] $filesJson is not a JSON list\n");

        return 2;
    }
    $absoluteFiles = [];
    foreach ($listed as $item) {
        if (!is_string($item)) {
            continue;
        }
        $absolute = normalize_path($item);
        if (is_file($absolute)) {
            $absoluteFiles[$absolute] = true;
        }
    }
    $absoluteFiles = array_keys($absoluteFiles);
    sort($absoluteFiles);
    if ($absoluteFiles === []) {
        fwrite(STDERR, "[php-reference] no source file in $filesJson\n");

        return 2;
    }
    if ($root === null) {
        $common = dirname($absoluteFiles[0]);
        foreach ($absoluteFiles as $file) {
            while ($common !== '' && !str_starts_with($file, rtrim($common, '/') . '/')) {
                $parent = dirname($common);
                if ($parent === $common) {
                    $common = '';
                    break;
                }
                $common = $parent;
            }
        }
        $root = $common === '' ? '/' : $common;
    }
    if (!str_starts_with($root, '/')) {
        $root = getcwd() . '/' . $root;
    }
    $real = realpath($root);
    if ($real !== false) {
        $root = $real;
    }
    $root = normalize_path($root);
    $root = rtrim($root, '/');
    if ($root === '') {
        $root = '/';
    }
    $fileSet = [];
    $phpFiles = [];
    $bladeFiles = [];
    foreach ($absoluteFiles as $file) {
        $relative = relative_to($file, $root);
        if ($relative === null) {
            fwrite(STDERR, "[php-reference] outside the root: $file\n");
            continue;
        }
        $fileSet[$relative] = true;
        if (str_ends_with(strtolower($file), '.blade.php')) {
            $bladeFiles[] = $relative;
        } elseif (str_ends_with(strtolower($file), '.php')) {
            $phpFiles[] = $relative;
        }
    }
    sort($phpFiles);
    sort($bladeFiles);

    $psr4 = discover_psr4($root, $fileSet);
    $classIndex = [];
    $parsed = [];
    $parseErrors = 0;
    $stats = [
        'files' => count($fileSet),
        'php_files' => count($phpFiles),
        'blade_files' => count($bladeFiles),
        'declarations' => 0,
        'references' => 0,
        'resolved_references' => 0,
        'unresolved_references' => 0,
        'ambiguous_references' => 0,
        'views_resolved' => 0,
        'livewire_resolved' => 0,
        'edges' => 0,
        'accepted_edges' => 0,
        'view_roots' => 0,
        'namespaced_view_roots' => 0,
        'view_registrations' => 0,
        'livewire_registrations' => 0,
        'parse_errors' => 0,
    ];
    foreach ($phpFiles as $relative) {
        $code = @file_get_contents($root . '/' . $relative);
        if ($code === false) {
            $parseErrors++;
            continue;
        }
        try {
            $parser = new PhpFileParser($code, $root . '/' . $relative);
            $parsed[$relative] = ['language' => 'php'] + $parser->parse();
            $stats['declarations'] += count($parsed[$relative]['declarations']);
            $stats['view_registrations'] += count($parsed[$relative]['view_registrations']);
            $stats['livewire_registrations'] += count($parsed[$relative]['component_registrations']);
            foreach ($parsed[$relative]['declarations'] as $declaration) {
                $classIndex[strtolower($declaration['name'])][] = $relative;
            }
        } catch (Throwable $error) {
            $parseErrors++;
            fwrite(STDERR, '[php-reference] ' . $relative . ': ' . $error->getMessage() . "\n");
            $parsed[$relative] = [
                'language' => 'php', 'namespace' => '', 'uses' => [], 'declarations' => [],
                'references' => [], 'component_registrations' => [], 'view_registrations' => [],
            ];
        }
    }
    foreach ($phpFiles as $relative) {
        $parsed[$relative]['uses'] = $parsed[$relative]['uses'] ?: [];
    }
    $stats['parse_errors'] = $parseErrors;
    foreach ($classIndex as $key => $files) {
        $classIndex[$key] = array_values(array_unique($files));
    }

    // View roots.
    $defaultRoots = [];
    foreach (array_keys($fileSet) as $relative) {
        $position = strpos($relative, 'resources/views/');
        if ($position === false) {
            continue;
        }
        if ($position > 0 && $relative[$position - 1] !== '/') {
            continue;
        }
        $rootDirectory = substr($relative, 0, $position) . 'resources/views';
        $defaultRoots[$rootDirectory] = true;
    }
    $defaultRoots = array_keys($defaultRoots);
    sort($defaultRoots);
    $namespaceRoots = [];
    foreach ($phpFiles as $relative) {
        foreach ($parsed[$relative]['view_registrations'] as $registration) {
            $expression = $registration['expression'];
            $resolved = evaluate_path_expression($expression, $root . '/' . $relative, $root);
            if ($resolved === null || $resolved === '') {
                continue;
            }
            $namespaceRoots[$registration['namespace']][$resolved] = true;
        }
    }
    foreach ($namespaceRoots as $namespace => $roots) {
        $namespaceRoots[$namespace] = array_keys($roots);
        sort($namespaceRoots[$namespace]);
    }
    // <module>/resources/views is the root of namespace <kebab-case module>.
    $allRoots = $defaultRoots;
    foreach ($namespaceRoots as $roots) {
        $allRoots = array_merge($allRoots, $roots);
    }
    foreach (array_unique($allRoots) as $rootDirectory) {
        if (!preg_match('#^(.*?)/?resources/views$#', $rootDirectory, $match) || $match[1] === '') {
            continue;
        }
        $module = basename($match[1]);
        if ($module === '' || $module === '.') {
            continue;
        }
        $namespace = kebab_case($module);
        if (!in_array($rootDirectory, $namespaceRoots[$namespace] ?? [], true)) {
            $namespaceRoots[$namespace][] = $rootDirectory;
        }
    }
    foreach ($namespaceRoots as $namespace => $roots) {
        sort($roots);
        $namespaceRoots[$namespace] = array_values(array_unique($roots));
    }
    $stats['view_roots'] = count($defaultRoots);
    $stats['namespaced_view_roots'] = count($namespaceRoots);

    // Accepted and not required: a Livewire component class and its conventional
    // template name each other in a running application. The graph records the
    // template edge in addition to the class edge; neither is a wrong edge.
    $acceptedEdges = [];
    foreach ($phpFiles as $relative) {
        foreach ($parsed[$relative]['declarations'] as $declaration) {
            $position = strpos($declaration['name'], '\\Livewire\\');
            if ($position === false) {
                continue;
            }
            $alias = livewire_alias_from_class(substr($declaration['name'], $position + strlen('\\Livewire\\')));
            [$target] = view_target('livewire.' . $alias, '', $namespaceRoots, $defaultRoots, $fileSet);
            if ($target !== null && $target !== $relative) {
                $acceptedEdges[$relative . "\0" . $target] = true;
            }
        }
    }

    // Livewire registrations, resolved through the use map of their file.
    $livewire = [];
    foreach ($phpFiles as $relative) {
        foreach ($parsed[$relative]['component_registrations'] as $registration) {
            $class = resolve_reference_name($registration['class'], $registration['ns'], $registration['uses']);
            $target = null;
            if ($class !== null) {
                [$target] = class_target($class, $classIndex, $psr4, $fileSet);
            }
            $livewire[strtolower($registration['alias'])][] = $target;
        }
    }

    // Resolve every reference of every file.
    $edges = [];
    $views = [];
    $ambiguousClasses = [];
    $classes = [];
    foreach ($classIndex as $key => $files) {
        if (count($files) === 1) {
            $files = array_values($files);
            $classes[$key] = $files[0];
            continue;
        }
        $ambiguousClasses[$key] = $files;
    }
    $orderedFiles = array_merge($phpFiles, $bladeFiles);
    sort($orderedFiles);
    $outputFiles = [];
    foreach ($orderedFiles as $relative) {
        if (str_ends_with(strtolower($relative), '.blade.php')) {
            $code = @file_get_contents($root . '/' . $relative);
            if ($code === false) {
                continue;
            }
            $scanner = new BladeScanner($code);
            $references = [];
            foreach ($scanner->scan() as $reference) {
                if ($reference['kind'] === 'livewire') {
                    [$target, $status, $candidates] = livewire_target(
                        $reference['raw'],
                        $livewire,
                        $psr4,
                        $classIndex,
                        $fileSet,
                        $namespaceRoots,
                        $defaultRoots
                    );
                } else {
                    [$target, $status, $candidates] = view_target(
                        $reference['raw'],
                        null,
                        $namespaceRoots,
                        $defaultRoots,
                        $fileSet
                    );
                }
                $entry = ['raw' => $reference['raw'], 'kind' => $reference['kind'], 'line' => $reference['line']];
                if ($target !== null) {
                    $entry['target'] = $target;
                    if ($target !== $relative) {
                        $edges[$relative . "\0" . $target] = true;
                    }
                }
                if ($reference['kind'] === 'livewire' && $target !== null) {
                    [$template] = view_target('livewire.' . $reference['raw'], '', $namespaceRoots, $defaultRoots, $fileSet);
                    if ($template !== null && $template !== $relative) {
                        $acceptedEdges[$relative . "\0" . $template] = true;
                    }
                }
                $entry['status'] = $status;
                if ($status === 'ambiguous') {
                    $entry['candidates'] = $candidates;
                }
                $references[] = $entry;
                $stats['references']++;
                $stats[$status . '_references']++;
                if ($status === 'resolved') {
                    if ($reference['kind'] === 'livewire') {
                        $stats['livewire_resolved']++;
                    } else {
                        $stats['views_resolved']++;
                    }
                }
            }
            $outputFiles[$relative] = ['language' => 'blade', 'references' => $references];
            continue;
        }
        $file = $parsed[$relative];
        $references = [];
        $seen = [];
        foreach ($file['references'] as $reference) {
            $entry = ['raw' => $reference['raw'], 'kind' => $reference['kind'], 'line' => $reference['line']];
            if ($reference['kind'] === 'view') {
                [$target, $status, $candidates] = view_target(
                    $reference['raw'],
                    null,
                    $namespaceRoots,
                    $defaultRoots,
                    $fileSet
                );
                if ($target !== null) {
                    $views[$reference['raw']] = $target;
                }
            } else {
                $candidates = [];
                $fqcn = null;
                if ($reference['kind'] === 'use') {
                    $fqcn = ltrim($reference['raw'], '\\');
                } else {
                    $fqcn = resolve_reference_name($reference['raw'], $reference['ns'], $reference['uses']);
                }
                if ($fqcn === null) {
                    $status = 'unresolved';
                    $target = null;
                } else {
                    [$target, $status, $candidates] = class_target($fqcn, $classIndex, $psr4, $fileSet);
                    $entry['name'] = $fqcn;
                }
            }
            if ($target !== null) {
                $entry['target'] = $target;
                if ($target !== $relative) {
                    $edges[$relative . "\0" . $target] = true;
                }
            }
            $entry['status'] = $status;
            if ($status === 'ambiguous') {
                $entry['candidates'] = $candidates;
            }
            $references[] = $entry;
            $stats['references']++;
            $stats[$status . '_references']++;
        }
        $outputFiles[$relative] = [
            'language' => 'php',
            'namespace' => $file['namespace'],
            'uses' => $file['uses'],
            'declarations' => $file['declarations'],
            'references' => $references,
        ];
    }
    $edgeList = [];
    foreach (array_keys($edges) as $key) {
        [$source, $target] = explode("\0", $key, 2);
        $edgeList[] = [$source, $target];
    }
    sort($edgeList);
    $acceptedList = [];
    foreach (array_keys($acceptedEdges) as $key) {
        [$source, $target] = explode("\0", $key, 2);
        if (!isset($edges[$key])) {
            $acceptedList[] = [$source, $target];
        }
    }
    sort($acceptedList);
    $stats['edges'] = count($edgeList);
    $stats['accepted_edges'] = count($acceptedList);

    $output = [
        'schema' => REFERENCE_SCHEMA,
        'stats' => $stats,
        'roots' => [
            'default_views' => $defaultRoots,
            'namespaced_views' => $namespaceRoots,
        ],
        'classes' => $classes,
        'ambiguous_classes' => $ambiguousClasses,
        'views' => $views,
        'files' => $outputFiles,
        'edges' => $edgeList,
        'accepted_edges' => $acceptedList,
    ];
    $encoded = json_encode($output, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    if ($encoded === false) {
        fwrite(STDERR, "[php-reference] cannot encode the output\n");

        return 1;
    }
    if (file_put_contents($outJson, $encoded . "\n") === false) {
        fwrite(STDERR, "[php-reference] cannot write $outJson\n");

        return 1;
    }
    printf(
        "files %d (php %d, blade %d) | declarations %d | references %d (resolved %d, unresolved %d, ambiguous %d) | edges %d | view roots %d (+%d namespaced)\n",
        $stats['files'],
        $stats['php_files'],
        $stats['blade_files'],
        $stats['declarations'],
        $stats['references'],
        $stats['resolved_references'],
        $stats['unresolved_references'],
        $stats['ambiguous_references'],
        $stats['edges'],
        $stats['view_roots'],
        $stats['namespaced_view_roots']
    );

    return 0;
}

exit(main(array_slice($argv, 1)));