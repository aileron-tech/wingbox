<?php

namespace App\Http\Controllers;

use App\Livewire\Counter;
use Illuminate\Support\Facades\View;

class ViewController
{
    public function home()
    {
        return view('pages.home', ['counter' => new Counter()]);
    }

    public function alert()
    {
        return View::make('components.alert');
    }

    public function notice()
    {
        return view()->make('livewire.notice');
    }
}
