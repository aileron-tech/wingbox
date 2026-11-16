<!DOCTYPE html>
<html>
<head>
    <title>@yield('title')</title>
    @livewireStyles
</head>
<body>
    @include('components.alert')
    <main>@yield('content')</main>
    @livewireScripts
</body>
</html>
