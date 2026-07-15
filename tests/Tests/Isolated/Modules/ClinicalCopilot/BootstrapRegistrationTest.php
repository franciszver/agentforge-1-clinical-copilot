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
use PHPUnit\Framework\Attributes\Test;
use PHPUnit\Framework\TestCase;
use Symfony\Component\EventDispatcher\EventDispatcher;

class BootstrapRegistrationTest extends TestCase
{
    private string $projectDir;
    private string $moduleBootstrapPath;

    protected function setUp(): void
    {
        $this->projectDir = dirname(__DIR__, 5);
        $this->moduleBootstrapPath = $this->projectDir . DIRECTORY_SEPARATOR . 'interface' . DIRECTORY_SEPARATOR . 'modules' . DIRECTORY_SEPARATOR . 'custom_modules' . DIRECTORY_SEPARATOR . 'oe-module-clinical-copilot' . DIRECTORY_SEPARATOR . 'src';
    }

    #[Test]
    public function testBootstrapClassExists(): void
    {
        $classLoader = new ModulesClassLoader($this->projectDir);
        $classLoader->registerNamespaceIfNotExists(
            'OpenEMR\\Modules\\ClinicalCopilot\\',
            $this->moduleBootstrapPath
        );

        $this->assertTrue(class_exists('OpenEMR\\Modules\\ClinicalCopilot\\Bootstrap'), 'Bootstrap class should exist after namespace registration');
    }

    #[Test]
    public function testBootstrapRegistersNamespaceViaModulesClassLoader(): void
    {
        $classLoader = new ModulesClassLoader($this->projectDir);

        // Register the namespace - should not throw
        $result = $classLoader->registerNamespaceIfNotExists(
            'OpenEMR\\Modules\\ClinicalCopilot\\',
            $this->moduleBootstrapPath
        );

        // Verify the class can be loaded after registration
        $this->assertTrue(class_exists('OpenEMR\\Modules\\ClinicalCopilot\\Bootstrap'), 'Bootstrap class should be loadable after namespace registration');
    }

    #[Test]
    public function testBootstrapSubscribesToEvents(): void
    {
        $classLoader = new ModulesClassLoader($this->projectDir);
        $classLoader->registerNamespaceIfNotExists(
            'OpenEMR\\Modules\\ClinicalCopilot\\',
            $this->moduleBootstrapPath
        );

        $eventDispatcher = new EventDispatcher();
        $bootstrapClass = 'OpenEMR\\Modules\\ClinicalCopilot\\Bootstrap';
        $bootstrap = new $bootstrapClass($eventDispatcher);

        // Call subscribeToEvents - should not throw
        $result = $bootstrap->subscribeToEvents();
        $this->assertNull($result, 'subscribeToEvents should complete without error and return null');
    }
}
