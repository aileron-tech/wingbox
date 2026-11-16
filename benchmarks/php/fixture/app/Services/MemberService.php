<?php

namespace App\Services;

use App\Models\{BaseModel as RootModel, Team};
use App\Models\User as Member;

class MemberService
{
    public function all(): array
    {
        return [new Member(), new Team()];
    }

    public function root(): RootModel
    {
        return new RootModel();
    }
}
