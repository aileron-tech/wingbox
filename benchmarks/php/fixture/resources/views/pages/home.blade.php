@extends('layouts.app')

@section('content')
    {{-- @include('ignored.comment') --}}
    @include('components.alert')
    @includeWhen($showAlert, 'components.alert')
    @includeIf('layouts.app')
    @includeFirst(['components.alert', 'emails.welcome'])
    @each('emails.welcome', $items, 'item')
    @includeIf('pages.home')
    <livewire:counter />
    <livewire:notice />
    <livewire:foo-bar />
    <livewire:admin.panel />
    <livewire:vendor::thing />
    <x-alert type="info" />
    <x-slot name="header">Ignored</x-slot>
    <x-blog::input name="email" />
    <x-dynamic-component :component="'components.alert'" />
@endsection
