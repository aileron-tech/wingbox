<?php

namespace App\Tests\Fixtures;

use Acme\Blog\Post;

class PostFactory
{
    public function make(): Post
    {
        return new Post();
    }
}
