<?php

namespace App\Services;

class GhostProbe
{
    public function name(): string
    {
        return \Acme\Blog\Ghost::class;
    }
}
