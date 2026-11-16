<?php

namespace App\Models;

use Acme\Blog\Post;
use App\Contracts\Auditable;
use App\Models\Concerns\HasUlids;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Foundation\Auth\User as Authenticatable;

class User extends Authenticatable implements Auditable
{
    use HasFactory;
    use HasUlids;

    protected ?Team $team = null;

    public function posts(): \Illuminate\Database\Eloquent\Relations\HasMany
    {
        return $this->hasMany(Post::class);
    }

    public function team(): ?Team
    {
        return $this->team;
    }

    public static function make(): static
    {
        return new static();
    }
}
