<?php

namespace App\Contracts;

interface Auditable
{
    public function auditName(): string;
}
