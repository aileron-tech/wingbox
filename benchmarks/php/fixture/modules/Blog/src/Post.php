<?php

namespace Acme\Blog;

use App\Models\User;

class Post
{
    public function author(): User
    {
        return new User();
    }
}
