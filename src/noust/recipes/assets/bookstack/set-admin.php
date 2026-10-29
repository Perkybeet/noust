<?php
// Gives the account a new BookStack installation starts with (admin@admin.com, password "password")
// the email and the password the recipe generated, as soon as the database has been migrated and
// seeded. Run by the stack's second service until it succeeds; once the factory account is gone it
// changes nothing, so an administrator's own changes are never overwritten by a restart or an update.
//
// Exit 1 means "not yet": the database does not answer, the tables are not there, or nothing is seeded.

$factory = 'admin@admin.com';
$email = getenv('ADMIN_EMAIL');
$password = getenv('ADMIN_PASSWORD');
if ($email === false || $email === '' || $password === false || $password === '') {
    fwrite(STDERR, "ADMIN_EMAIL and ADMIN_PASSWORD are needed\n");
    exit(2);
}

try {
    $pdo = new PDO(
        sprintf('mysql:host=%s;dbname=%s', getenv('DB_HOST'), getenv('DB_DATABASE')),
        getenv('DB_USERNAME'),
        getenv('DB_PASSWORD'),
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
    );
    if ((int) $pdo->query('SELECT COUNT(*) FROM users')->fetchColumn() === 0) {
        exit(1);
    }
    $change = $pdo->prepare('UPDATE users SET email = ?, password = ? WHERE email = ?');
    $change->execute([$email, password_hash($password, PASSWORD_BCRYPT), $factory]);
} catch (Throwable $e) {
    exit(1);
}
exit(0);
