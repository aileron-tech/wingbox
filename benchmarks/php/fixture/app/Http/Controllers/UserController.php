<?php

namespace App\Http\Controllers;

use App\Contracts\Notifier;
use App\Exceptions\Handler;
use App\Models\BaseModel;
use App\Models\Team;
use App\Models\User;
use App\Routing\Route;
use Illuminate\Http\Request;

final class UserController
{
    public function __construct(private readonly Notifier $notifier)
    {
    }

    #[Route('/users/{id}', methods: ['GET'])]
    public function show(Request $request, Team $team): User
    {
        $user = User::query()->findOrFail($request->route('id'));

        try {
            $this->notifier->notify($user);
        } catch (Handler $error) {
            report($error);
        }

        return $user;
    }

    public function make(): User
    {
        return new User();
    }
}
