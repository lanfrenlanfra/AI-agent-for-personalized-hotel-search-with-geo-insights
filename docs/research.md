# How others do it and what we took

**English** | [Русский](research.ru.md)

A short overview of public sources (links at the bottom). What they **state directly** and what is our own inference are kept separate.

## Major platforms

| Who | What it does (per public sources) | What we took |
|---|---|---|
| **Booking.com** | "Smart Filter": the guest describes the ideal property in their own words ("hotels in Amsterdam with a good gym, a rooftop bar and a canal view from the room"), and a generative model picks the filters. "Property Q&A": answers to questions about a specific property ("is there an EV charger", "are large dogs allowed") are built from the description, reviews and photos. Review summaries are in testing (announced Oct 30, 2024). | A natural-language request → criteria ("chips") that the guest sees and can remove. The answer is built from several sources at once — but in our case every answer also shows **where it comes from** and **how fresh it is**. |
| **Airbnb** | LAEP (Listing Attribute Extraction Platform): amenities, services and location features are extracted from the host's free text — NER, matching a phrase to a standard attribute by cosine similarity of embeddings, then a "yes / unknown / no" classifier with a confidence score and a threshold. Separately, computer vision on photos: room type, aesthetics, embeddings (the photo article does not claim that photos are used to verify what is declared). | The same ternary view: "yes / no / unknown" — absence of data ≠ "no". Confidence is stored with every value. |
| **Agoda** | May 2026: hotel photos are shown next to reviews by topic. The "thematic approach" links both photos and reviews to shared attributes (breakfast, pool, location); up to 15 photos per topic, quotes in the guest's language and shares of positive/negative; the company claims 700 million images and 40+ languages, an "AI-powered multimodal content system". This is a company press release; there is no independent accuracy assessment. | The idea that "the same claim is confirmed by both text and picture": for us, independent sources likewise reinforce each other, and links to photos and articles are shown in "How we know this". |
| **Shadowmap** (shadow and sun tool) | According to its own page: buildings — Overture (default) or OSM/Google 3D, terrain — Nextzen (default) and other models, vegetation — OSM, solar analytics — based on 20 years of data. The page gives no accuracy figures for building data and, when data is incomplete, suggests editing heights and trees manually. | The same set of layers (terrain + buildings + vegetation) for "where is the sun"; but, unlike the tool, **our confidence depends on layer quality** (share of explicit heights, presence of terrain), and this is stated in the result. |
| **Expedia Group** | Generative review summaries and an in-app assistant (per press materials). Nothing is public about how attributes are extracted. | — |

**What we did not find in public sources:** none of the platforms describes (a) computing "window view / sunset / noise by building side" from geodata, (b) an explicit check of whether the hotel's claims are outdated against fresh reviews, with the conflict shown to the user, or (c) a dialog with the hotel with a free-text answer parsed into fields. This is where our solution sits; we cannot claim that others don't have this internally — only that it isn't publicly described.

## Research the design relies on

* **Knowledge-Based Trust** (Dong et al., Google, 2015). Trust in a source is estimated by the share of correct facts, and *extraction* errors are separated from *source* errors by joint inference. Our `reconcile.py` is a simplified version: each source has its own base reliability (`SOURCES`), independent sources reinforce each other (noisy-OR), and the leader and the opposition are compared by weight, so the system does not silently pick a winner when there is a noticeable contradiction.
* **Latent Aspect Rating Analysis** (Wang, Lu, Zhai, KDD 2010) and later work on hotel **aspect-based sentiment analysis** (cleanliness, location, noise, staff): a review is not a single rating but a set of opinions on aspects. We extract aspect mentions with polarity, handle negation ("no elevator"), the "cons" section and review age (half-life of 540 days).
* **Truth discovery / data fusion** — the general framework: sources conflict, so weights, dates and an explainable result are needed.

## Algorithms we took

* **r.horizon (GRASS GIS)**: "computes the horizon angle from a digital elevation model"; it walks along the ray from the observation point, raises the angle when terrain blocks it, and accounts for Earth's curvature — distant terrain appears lower. We implemented the same technique in numpy (`app/geo/terrain.py`) plus atmospheric refraction; we do not use the ready-made GRASS module so that the project starts with a single `docker compose up`.
* **Tobler's hiking function (Tobler, 1993)**: walking speed as a function of slope; used for walking time with climbs. The parameters (the formula and the 0.3–1.25 limits relative to base speed) are our own tuning and have not been calibrated on real routes.

## What we could not verify

Noise and "neighborhood quality" services (for example HowLoud) — we could not retrieve pages describing their method, so we do not cite them and make no claims about their method. The descriptions of Agoda and Shadowmap are the companies' own statements.

## What follows for the product

1. **Every value is an observation** with a source, confidence, evidence date and a quote. The result (fact) is always derived and recomputed.
2. **Freshness is part of trust.** Every characteristic has a lifetime (`ttl_days`): quietness — 180 days, elevator presence — 365, etc. Weight falls with age, the value "ages", and this is visible to the user and triggers a question for the hotelier.
3. **Map calculation checks the words.** If the hotelier claims "the sunset is visible from the 3rd floor" and the 3D model of the block does not allow it, the fact gets the status "disputed", not "confirmed".
4. **The model must not look more confident than it is.** A value computed only from OSM (without independent confirmation) does not get the status "confirmed"; neither does a simulated hotelier in the demo.

5. **Don't check everything: two-stage selection.** Booking.com and similar platforms translate a request into filters over the inventory and perform the expensive analysis for a narrow set; we do the same: a cheap region index → candidates for the query → background deep check with caching and limits. We do not know how other systems work internally and do not claim to; this is our own technique in the same spirit.

## Sources

* Agoda, press release "Agoda Makes it Easier for Travelers to See Relevant Photos in Reviews" (May 6, 2026): https://kyodonewsprwire.jp/release/202605068593
* Shadowmap, "Shadow Analysis Tools Compared": https://shadowmap.org/learn/shadow-analysis-tools-comparison
* GRASS GIS, r.horizon manual: https://grass.osgeo.org/grass85/manuals/r.horizon.html
* Open-Meteo, Air Quality API documentation: https://open-meteo.com/en/docs/air-quality-api

* Booking.com, press release "Booking.com Enhances Travel Planning with New AI-Powered Features" (Oct 30, 2024): https://www.bookingholdings.com/press-releases/booking-com-enhances-travel-planning-with-new-ai-powered-features-for-easier-smarter-decisions/
* Airbnb Engineering, Listing Attribute Extraction Platform (analysis): https://besthub.dev/articles/airbnb-s-listing-attribute-extraction-platform-laep-end-to-end-structured-information-extraction-using-machine-learning-and-nlp-dd58eb4f8b12
* Airbnb Engineering, "When a Picture Is Worth More Than Words": https://medium.com/airbnb-engineering/when-a-picture-is-worth-more-than-words-17718860dcc2
* Dong X. L. et al., "Knowledge-Based Trust: Estimating the Trustworthiness of Web Sources", 2015: https://arxiv.org/abs/1502.03519
* Wang H., Lu Y., Zhai C., "Latent Aspect Rating Analysis on Review Text Data", KDD 2010: https://www.cs.virginia.edu/~hw5x/Course/TextMining-2019Spring/_site/docs/PDFs/Latent%20Aspect%20Rating%20Analysis.pdf
