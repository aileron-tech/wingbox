<?php

namespace App\Models\Concerns;

trait HasUlids
{
    public function newUniqueId(): string
    {
        return strtolower((string) str()->ulid());
    }
}
