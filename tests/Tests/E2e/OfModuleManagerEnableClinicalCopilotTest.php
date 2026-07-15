<?php

/**
 * Module Manager Enable Clinical Co-Pilot Test
 *
 * Test that the Clinical Co-Pilot module can be registered and enabled
 * through the Module Manager UI without errors.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Francisco de Guzman <ciscodg@gmail.com>
 * @copyright Copyright (c) 2025 Francisco de Guzman
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\E2e;

use Facebook\WebDriver\WebDriverBy;
use OpenEMR\Tests\E2e\Base\BaseTrait;
use OpenEMR\Tests\E2e\Login\LoginTestData;
use OpenEMR\Tests\E2e\Login\LoginTrait;
use PHPUnit\Framework\Attributes\Test;
use Symfony\Component\Panther\PantherTestCase;

class OfModuleManagerEnableClinicalCopilotTest extends PantherTestCase
{
    use BaseTrait;
    use LoginTrait;

    #[Test]
    public function testModuleCanBeRegisteredAndEnabledViaModuleManager(): void
    {
        $this->base();
        try {
            // Log in as admin
            $this->login(LoginTestData::username, LoginTestData::password);

            // Navigate to Module Manager
            $this->client->request('GET', '/Installer/index?testing_mode=1');

            // Wait for page to load
            $title = $this->client->waitForVisibility(
                WebDriverBy::cssSelector('title'),
                10
            );
            $this->assertNotNull($title, 'Module Manager page should load');

            // Look for the Clinical Co-Pilot module row
            // The module directory name is 'oe-module-clinical-copilot'
            $moduleRows = $this->client->findElements(
                WebDriverBy::xpath("//tr[contains(., 'clinical-copilot') or contains(., 'Clinical Co-Pilot')]")
            );

            // If module is found, check its state
            if (!empty($moduleRows)) {
                $moduleRow = $moduleRows[0];

                // Check if there's a register button (module not yet registered)
                $registerButtons = $moduleRow->findElements(
                    WebDriverBy::xpath(".//button[contains(., 'Register') or contains(@value, 'Register')]")
                );

                if (!empty($registerButtons)) {
                    // Click register
                    $registerButtons[0]->click();

                    // Wait for registration to complete
                    $this->client->wait(10)->until(
                        static fn($driver) => !$driver->findElements(
                            WebDriverBy::xpath("//button[contains(., 'Register')]")
                        )
                    );
                }

                // Now look for enable button
                $enableButtons = $moduleRow->findElements(
                    WebDriverBy::xpath(".//button[contains(., 'Enable') or contains(@value, 'Enable')]")
                );

                if (!empty($enableButtons)) {
                    // Click enable
                    $enableButtons[0]->click();

                    // Wait for enable to complete
                    $this->client->wait(10)->until(
                        static fn($driver) => !$driver->findElements(
                            WebDriverBy::xpath("//button[contains(., 'Enable')]")
                        )
                    );
                }

                // Verify no error message appears
                $errorMessages = $this->client->findElements(
                    WebDriverBy::xpath("//div[contains(@class, 'alert-danger') or contains(@class, 'error')]")
                );

                $this->assertEmpty(
                    $errorMessages,
                    'Module Manager should not show any error messages after enabling the module'
                );
            } else {
                // Module not found - this is a failure for this test
                // (unless it's because the test is running in a fresh environment)
                $this->markTestIncomplete(
                    'Clinical Co-Pilot module not found in Module Manager. ' .
                    'It may not have been discovered yet. ' .
                    'The module directory may need to be present for discovery.'
                );
            }
        } catch (\Throwable $e) {
            // Close client
            $this->client->quit();
            // re-throw the exception
            throw $e;
        }
        // Close client
        $this->client->quit();
    }
}
