# Synthetic Laravel fixture

A small Laravel-shaped project that exercises the parts of the PHP benchmark
harness that are easy to get wrong. It is not a corpus: numbers produced from it
show that the harness runs, not how Wingbox behaves on real projects.

What it covers:

- two PSR-4 prefixes in the root `composer.json` (`App\` -> `app/`, `Acme\` ->
  `modules/Acme/src/`) and a nested `modules/Blog/composer.json`
  (`Acme\Blog\` -> `src/`), with `autoload` and `autoload-dev`;
- the longest prefix wins: `Acme\Blog\Ghost` resolves through the nested
  `Acme\Blog\` map even though a shorter `Acme\` file exists;
- a duplicate declaration where the PSR-4 file wins (a generated IDE-helper
  stub next to the real model file), and a genuinely ambiguous class that
  resolves to nothing;
- Blade layouts, `@extends`, `@include`, `@includeIf`, `@includeWhen`,
  `@includeFirst`, `@each`, `@component`, anonymous `<x-*>` components,
  `<x-namespace::name>`, `<x-dynamic-component>` and `<x-slot>`;
- two view roots that make `emails.welcome` ambiguous, and a view namespace
  registered with `loadViewsFrom` (`blog::...`);
- Livewire through a `Livewire::component` registration, the
  `App\Livewire\<Studly>` convention, kebab and nested aliases
  (`foo-bar`, `admin.panel`), a view-only alias (`notice`) and a package alias
  (`vendor::thing`) that must not resolve locally;
- a `view()`, `View::make()` and `view()->make()` call, Eloquent associations,
  attributes, catch types, trait use, union and nullable types.

Run the reference resolver on it:

```sh
python3 benchmarks/php/tools/list_files.py benchmarks/php/fixture <work dir>/files.json
php benchmarks/php/tools/php_reference.php <work dir>/files.json <work dir>/ref.json \
  --root benchmarks/php/fixture
```

The expected edges are the ones the harness's own smoke test relies on; see
`benchmarks/php/tools/php_reference.php` for the rules and `benchmarks/php/README.md`
for the benchmark itself.