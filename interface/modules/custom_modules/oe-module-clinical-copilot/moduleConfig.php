<?php

/**
 * Clinical Co-Pilot Module Configuration
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Francisco de Guzman <ciscodg@gmail.com>
 * @copyright Copyright (c) 2025 Francisco de Guzman
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

return [
    'name' => 'Clinical Co-Pilot Agent',
    'description' => 'Intelligent clinical decision support and workflow optimization powered by Claude AI',
    'version' => '1.0.0',
    'author' => 'Francisco de Guzman',
    'email' => 'ciscodg@gmail.com',
    'license' => 'GPL-3.0',
    'acl_category' => 'admin',
    'acl_section' => 'users',

    // Module dependencies
    'require' => [
        'openemr' => '>=7.0.0',
    ],
];
