<?php

namespace App\Services;

use App\Shared\Ambiguous;

class AmbiguousProbe
{
    public function make(): Ambiguous
    {
        return new Ambiguous();
    }
}
