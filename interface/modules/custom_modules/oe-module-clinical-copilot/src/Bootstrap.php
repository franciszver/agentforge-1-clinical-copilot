<?php

/**
 * Clinical Co-Pilot Module Bootstrap Class
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Francisco de Guzman <ciscodg@gmail.com>
 * @copyright Copyright (c) 2025 Francisco de Guzman
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

use OpenEMR\Common\Session\PatientSessionUtil;
use OpenEMR\Events\Patient\Summary\Card\RenderEvent as PatientSummaryCardRenderEvent;
use OpenEMR\Events\UserInterface\PageHeadingRenderEvent;
use OpenEMR\Modules\ClinicalCopilot\Controller\CopilotPanelController;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;

class Bootstrap
{
    const MODULE_INSTALLATION_PATH = "/interface/modules/custom_modules/oe-module-clinical-copilot";
    const MODULE_NAME = "oe-module-clinical-copilot";

    /**
     * Page ID for the patient demographics/dashboard screen, as dispatched
     * by OemrUI::pageHeading() (see interface/patient_file/summary/demographics.php).
     */
    private const PATIENT_DASHBOARD_PAGE_ID = 'core.mrd';

    /**
     * PatientSummaryCard\RenderEvent::EVENT_HANDLE fires once per existing
     * dashboard card (note, reminder, lab, ...), each individually gated by
     * its own ACL/feature-flag check. Render the Co-Pilot card on whichever
     * fires first so injection doesn't depend on any single card being
     * enabled for the current user.
     */
    private bool $cardRendered = false;

    /**
     * The module's CSS/JS asset tags only need to be emitted once per
     * page, from whichever of the two listeners below fires first.
     */
    private bool $assetsRendered = false;

    public function __construct(
        /**
         * @var EventDispatcherInterface The object responsible for sending and subscribing to events.
         * Public so the scaffold carries the wiring for the first real event
         * subscription (P2.12) without a write-only private property.
         */
        public readonly EventDispatcherInterface $eventDispatcher
    ) {
    }

    /**
     * Subscribe to events.
     *
     * @return void
     */
    public function subscribeToEvents(): void
    {
        $this->eventDispatcher->addListener(
            PatientSummaryCardRenderEvent::EVENT_HANDLE,
            $this->renderCopilotCard(...)
        );
        $this->eventDispatcher->addListener(
            PageHeadingRenderEvent::EVENT_PAGE_HEADING_RENDER,
            $this->renderOpenChatButton(...)
        );
    }

    /**
     * Inject the Co-Pilot card onto the patient dashboard.
     *
     * @param PatientSummaryCardRenderEvent $event
     * @return void
     */
    public function renderCopilotCard(PatientSummaryCardRenderEvent $event): void
    {
        if ($this->cardRendered) {
            return;
        }
        if (PatientSessionUtil::getPid() <= 0) {
            return;
        }
        $this->cardRendered = true;

        $controller = new CopilotPanelController();
        echo $this->renderAssetsOnce($controller);
        echo $controller->renderCard();
    }

    /**
     * Inject the persistent open-chat button into the patient dashboard's
     * page heading.
     *
     * @param PageHeadingRenderEvent $event
     * @return PageHeadingRenderEvent
     */
    public function renderOpenChatButton(PageHeadingRenderEvent $event): PageHeadingRenderEvent
    {
        if ($event->getPageId() !== self::PATIENT_DASHBOARD_PAGE_ID) {
            return $event;
        }

        $controller = new CopilotPanelController();
        $event->appendTitleNavContent($this->renderAssetsOnce($controller) . $controller->renderOpenChatButton());

        return $event;
    }

    private function renderAssetsOnce(CopilotPanelController $controller): string
    {
        if ($this->assetsRendered) {
            return '';
        }
        $this->assetsRendered = true;

        return $controller->renderAssetTags();
    }
}
