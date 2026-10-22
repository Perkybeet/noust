<?php
/**
 * WordPress configuration, written by Noust.
 *
 * Every value comes from the application's environment, which Noust keeps in
 * shared/.env and hands to its PHP-FPM pool. Change one with
 * `noust env set <domain> NAME=value` followed by `noust update <domain>`,
 * not here. This file lives in shared/ and is linked into every release.
 */

function noust_env( $name, $default = '' ) {
	$value = getenv( $name );
	return ( false === $value || '' === $value ) ? $default : $value;
}

define( 'DB_NAME', noust_env( 'WORDPRESS_DB_NAME' ) );
define( 'DB_USER', noust_env( 'WORDPRESS_DB_USER' ) );
define( 'DB_PASSWORD', noust_env( 'WORDPRESS_DB_PASSWORD' ) );
define( 'DB_HOST', noust_env( 'WORDPRESS_DB_HOST', 'localhost' ) );
define( 'DB_CHARSET', 'utf8mb4' );
define( 'DB_COLLATE', '' );

define( 'AUTH_KEY', noust_env( 'WORDPRESS_AUTH_KEY' ) );
define( 'SECURE_AUTH_KEY', noust_env( 'WORDPRESS_SECURE_AUTH_KEY' ) );
define( 'LOGGED_IN_KEY', noust_env( 'WORDPRESS_LOGGED_IN_KEY' ) );
define( 'NONCE_KEY', noust_env( 'WORDPRESS_NONCE_KEY' ) );
define( 'AUTH_SALT', noust_env( 'WORDPRESS_AUTH_SALT' ) );
define( 'SECURE_AUTH_SALT', noust_env( 'WORDPRESS_SECURE_AUTH_SALT' ) );
define( 'LOGGED_IN_SALT', noust_env( 'WORDPRESS_LOGGED_IN_SALT' ) );
define( 'NONCE_SALT', noust_env( 'WORDPRESS_NONCE_SALT' ) );

$table_prefix = noust_env( 'WORDPRESS_TABLE_PREFIX', 'wp_' );

define( 'WP_DEBUG', 'true' === noust_env( 'WORDPRESS_DEBUG', 'false' ) );

// The files belong to the account PHP runs as, so updates and plugin
// installs write them directly instead of asking for FTP credentials.
define( 'FS_METHOD', 'direct' );

// wp-content is a link from the release into shared/. PHP resolves links in
// __FILE__, so without this a plugin's own path would not start with the
// plugins directory WordPress computes, and plugin URLs would break.
if ( defined( 'ABSPATH' ) && is_link( ABSPATH . 'wp-content' ) ) {
	define( 'WP_CONTENT_DIR', realpath( ABSPATH . 'wp-content' ) );
}

if ( ! defined( 'ABSPATH' ) ) {
	define( 'ABSPATH', __DIR__ . '/' );
}

require_once ABSPATH . 'wp-settings.php';
