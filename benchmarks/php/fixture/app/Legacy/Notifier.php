<?php

namespace App\Contracts {
    interface Notifier
    {
        public function notify(object $target): void;
    }
}
