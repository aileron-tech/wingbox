<?php

namespace App\Providers;

use App\Livewire\Counter;
use Illuminate\Support\ServiceProvider;
use Livewire\Livewire;

class AppServiceProvider extends ServiceProvider
{
    public function boot(): void
    {
        Livewire::component('counter', Counter::class);

        $this->loadViewsFrom(__DIR__.'/../../modules/Blog/resources/views', 'blog');
    }
}
