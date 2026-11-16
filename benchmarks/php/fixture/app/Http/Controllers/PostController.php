<?php

namespace App\Http\Controllers;

use Acme\Blog\Post;
use Acme\Blog\Tests\PostTest;
use App\Tests\Fixtures\PostFactory;

class PostController
{
    public function show(Post $post): Post
    {
        $factory = new PostFactory();
        $test = PostTest::class;

        return $post;
    }
}
