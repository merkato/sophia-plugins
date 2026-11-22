-- Panel warstw po otwarciu mapy / projektu: otwarty albo zamknięty (ogólnie i dla projektu).
ALTER TABLE wyglad.ustawienia
    ADD COLUMN panel_warstw text NOT NULL DEFAULT 'otwarty' CHECK (panel_warstw IN ('otwarty', 'zamkniety'));

ALTER TABLE wyglad.projekt
    ALTER COLUMN motyw DROP NOT NULL,          -- projekt może zmieniać tylko panel warstw
    ADD COLUMN panel_warstw text CHECK (panel_warstw IN ('otwarty', 'zamkniety')),
    ADD CHECK (motyw IS NOT NULL OR panel_warstw IS NOT NULL);
