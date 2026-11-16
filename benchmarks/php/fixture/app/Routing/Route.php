<?php

namespace App\Routing;

use Attribute;

#[Attribute(Attribute::TARGET_METHOD)]
final class Route
{
    public function __construct(public string $path, public array $methods = [])
    {
    }
}
