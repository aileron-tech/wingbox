<?php

namespace App\Livewire;

use App\Models\Team;
use Livewire\Component;

class Counter extends Component
{
    public int $count = 0;

    public function increment(): void
    {
        $this->count++;
    }

    public function teams(): array
    {
        return Team::query()->get()->all();
    }
}
