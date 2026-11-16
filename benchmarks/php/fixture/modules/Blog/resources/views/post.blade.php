@extends('layouts.app')

@section('content')
    @include('blog::components.input')
    @include('emails.welcome')
    <x-blog::input />
@endsection
