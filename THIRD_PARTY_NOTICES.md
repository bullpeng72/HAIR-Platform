# Third-Party Notices

HAIR-Platform itself is licensed under the MIT License (see `LICENSE`).
The components below are adapted from third-party work. No third-party code,
fonts, or binary assets are bundled; the adaptations are design rules, writing
rules, and design-token values re-expressed in HAIR-Platform's own code.

---

## korean-report-skills

Used in: `src/hair_platform/presentation_deck.py` ("프레젠테이션작성" skill and
the HAIR deck design system) — single-accent color tokens, surface (tile)
alternation, border-based callouts, rule-only tables, status badges, and Korean
report writing rules.

Source: https://github.com/JangHyun-bin/korean-report-skills

```
korean-report-skills
Copyright 2026 장현빈

This product includes software developed by 장현빈.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

Modifications: rules and token values were selected, translated into a single
CSS design system scoped to `.hair-deck`, and condensed into skill prompt text.

---

## build-html-ppt (build-html-slides)

Used in: `src/hair_platform/presentation_deck.py` — one claim per slide,
evidence-meaning headlines, composition-family variety, audience decision order,
the 1280×720 logical canvas with minimum type sizes, and a designed closing.

Source: https://github.com/mclub4/build-html-ppt

```
MIT License

Copyright (c) 2026 HYUNJIN CHO

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

build-html-ppt's Korean copy rules are themselves partly adapted from
[epoko77-ai/im-not-ai](https://github.com/epoko77-ai/im-not-ai)
(Copyright (c) 2026 epoko77-ai, MIT License, same terms as above).
