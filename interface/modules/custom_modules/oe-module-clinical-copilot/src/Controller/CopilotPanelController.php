<?php

/**
 * Clinical Co-Pilot Panel Controller
 *
 * Renders the inert UI shells injected onto the patient dashboard: the
 * Co-Pilot card and the persistent open-chat button, plus the module's
 * CSS/JS asset tags and the escaped session context they read.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Francisco de Guzman <ciscodg@gmail.com>
 * @copyright Copyright (c) 2026 Francisco de Guzman
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\Controller;

use OpenEMR\Common\Session\EncounterSessionUtil;
use OpenEMR\Common\Session\PatientSessionUtil;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Modules\ClinicalCopilot\Bootstrap;

final class CopilotPanelController
{
    private readonly int $pid;

    private readonly int $encounter;

    private readonly int $authUserId;

    private readonly string $moduleUrl;

    public function __construct()
    {
        $this->pid = PatientSessionUtil::getPid();
        $this->encounter = EncounterSessionUtil::getEncounter();

        $session = SessionWrapperFactory::getInstance()->getActiveSession();
        $rawAuthUserId = $session->get('authUserID');
        $this->authUserId = is_numeric($rawAuthUserId) ? (int) $rawAuthUserId : 0;

        $this->moduleUrl = OEGlobalsBag::getInstance()->getWebRoot() . Bootstrap::MODULE_INSTALLATION_PATH;
    }

    /**
     * Render the Co-Pilot card injected onto the patient dashboard.
     * Inert shell only - no chat behavior (see P2.14).
     */
    public function renderCard(): string
    {
        ob_start();
        ?>
        <div class="card copilot-card" id="copilot-card" data-copilot-pid="<?php echo attr((string) $this->pid); ?>">
            <div class="card-body p-1">
                <h6 class="card-title mb-0"><?php echo xlt('Co-Pilot'); ?></h6>
                <div class="copilot-card-body text-muted small">
                    <?php echo xlt('Clinical Co-Pilot assistant'); ?>
                </div>
            </div>
        </div>
        <?php
        return ob_get_clean();
    }

    /**
     * Render the persistent open-chat button injected into the page
     * heading, plus an inert placeholder panel it toggles.
     */
    public function renderOpenChatButton(): string
    {
        ob_start();
        ?>
        <button type="button"
                id="copilot-open-chat-btn"
                class="btn btn-sm btn-outline-primary copilot-open-chat-btn"
                title="<?php echo xla('Open Clinical Co-Pilot'); ?>"
                data-copilot-pid="<?php echo attr((string) $this->pid); ?>">
            <i class="fa fa-comment-medical"></i> <?php echo xlt('Co-Pilot'); ?>
        </button>
        <div id="copilot-chat-panel" class="copilot-chat-panel copilot-hidden" aria-hidden="true"></div>
        <?php
        return ob_get_clean();
    }

    /**
     * Render the module's CSS/JS asset tags and the current session
     * context (pid, encounter, authUserID) for the panel JS to read.
     *
     * The context values are already parsed to int by the session
     * accessors above, so they are safe primitives - json_encode() (the
     * same mechanism js_escape() wraps) is used directly here because the
     * composite array is not a string js_escape()'s signature accepts.
     */
    public function renderAssetTags(): string
    {
        $context = [
            'pid' => $this->pid,
            'encounter' => $this->encounter,
            'authUserID' => $this->authUserId,
        ];
        $contextJson = json_encode($context, JSON_THROW_ON_ERROR);
        ob_start();
        ?>
        <link rel="stylesheet" href="<?php echo attr($this->moduleUrl . '/public/assets/css/copilot.css'); ?>">
        <script>
            window.CopilotContext = <?php echo $contextJson; ?>;
        </script>
        <script src="<?php echo attr($this->moduleUrl . '/public/assets/js/copilot.js'); ?>" defer></script>
        <?php
        return ob_get_clean();
    }
}
