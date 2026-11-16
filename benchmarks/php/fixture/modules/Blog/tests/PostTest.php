<?php

namespace Acme\Blog\Tests;

use Acme\Blog\Post;

class PostTest
{
    public function testPost(): Post
    {
        return new Post();
    }
}
