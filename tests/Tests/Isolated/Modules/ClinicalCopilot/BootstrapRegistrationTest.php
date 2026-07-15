<?php

/**
 * Bootstrap Registration Test for Clinical Co-Pilot Module
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Francisco de Guzman <ciscodg@gmail.com>
 * @copyright Copyright (c) 2025 Francisco de Guzman
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\ClinicalCopilot;

use OpenEMR\Core\ModulesClassLoader;
use OpenEMR\Modules\ClinicalCopilot\Bootstrap;
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Symfony\Component\EventDispatcher\EventDispatcher;

class BootstrapRegistrationTest extends TestCase
{
    #[Test]
    public function testBootstrapClassExists(): void
    {
        $this->assertTrue(class_exists(Bootstrap::class), 'Bootstrap class should exist');
    }

    #[Test]
    public function testBootstrapRegistersNamespace(): void
    {
        $projectDir = dirname(__DIR__, 5);
        $classLoader = new ModulesClassLoader($projectDir);

        // Register the namespace
        $classLoader->registerNamespaceIfNotExists(
            'OpenEMR\\Modules\\ClinicalCopilot\\',
            $projectDir . DIRECTORY_SEPARATOR . 'interface' . DIRECTORY_SEPARATOR . 'modules' . DIRECTORY_SEPARATOR . 'custom_modules' . DIRECTORY_SEPARATOR . 'oe-module-clinical-copilot' . DIRECTORY_SEPARATOR . 'src'
        );

        // Verify the class can be loaded
        $this->assertTrue(class_exists(Bootstrap::class), 'Bootstrap class should be loadable after namespace registration');
    }

    #[Test]
    public function testBootstrapSubscribesToEvents(): void
    {
        $eventDispatcher = new EventDispatcher();
        $bootstrap = new Bootstrap($eventDispatcher);

        // Call subscribeToEvents - should not throw
        $this->assertNull($bootstrap->subscribeToEvents(), 'subscribeToEvents should complete without error');
    }
}
