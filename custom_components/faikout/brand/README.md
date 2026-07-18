# Brand assets

`icon.png` (256×256) and `icon@2x.png` (512×512), square PNG with transparency.

This location matters: HACS looks for `<integration path>/brand/icon.png` and uses it when present,
falling back to the [home-assistant/brands](https://github.com/home-assistant/brands) repository only if
it is missing. Shipping them here means the integration has an icon without a submission there.

The mark is original artwork for this integration: it deliberately uses neither Daikin's nor the Faikin
project's branding, and no Home Assistant branding either.
