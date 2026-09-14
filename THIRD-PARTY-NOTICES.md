# Third-Party Notices

`forecast-scaffold` is distributed under the PolyForm Noncommercial License 1.0.0 (see
[LICENSE](LICENSE)). It incorporates the third-party component below, which carries its own
license. That license governs that component and is not superseded by the project license;
the notice below must be retained in all copies and substantial portions.

---

## forecasting-tools

- **Component:** the percentile-to-CDF construction and its standardization constants, ported
  (rewritten in pure stdlib) into the `numeric CDF` section of `src/forecast_scaffold/core.py`
  and carried in that file's byte-identical vendored copies at `skills/forecast/scripts/fsj.py`
  and `skills/calibrate/scripts/fsj.py`. The in-file attribution marks the section.
- **Upstream:** <https://github.com/Metaculus/forecasting-tools>
  (`forecasting_tools/data_models/numeric_report.py`)
- **License:** MIT

The MIT License permits sublicensing, so this component may be redistributed as part of a
work offered under other terms. Its copyright and permission notice is reproduced in full:

```
MIT License

Copyright (c) 2024 CodexVeritas

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
