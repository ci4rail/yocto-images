# OSS clearing release procedure

The generated clearing archive is the technical evidence package for one exact
image build. It is deliberately offline and contains no confidentiality
marking.

## Contract requirement mapping

| Requirement | Delivered evidence |
| --- | --- |
| A.1–A.3 component, version, license | `THIRD_PARTY_SOFTWARE.html` and `components.csv` |
| A.4 full license text | Exact legal files collected and verified by Yocto, embedded in the HTML |
| A.5–A.6 copyright and acknowledgements | Exact component legal/notice files embedded in the HTML |
| B additional mandatory information | Component legal/notice files embedded without alteration |
| C corresponding source | Unpacked and patched source archives under `sources/<recipe>/<version>/` |
| D unambiguous assignment | Each installed package is mapped to its version, recipe, legal material, and source archive |

The SPDX 2.2 SBOM and original Yocto license manifest are included as supporting
machine-readable evidence.

## Release gate

For every customer release:

1. Run the image's `compliance` Make target, or let the GitHub release workflow
   generate the clearing archive.
2. Confirm that bundle creation succeeds. Missing legal material or source for
   any shipped non-closed recipe is a hard error.
3. Review changes in `components.csv` against the previous released image.
4. For every new or version-changed component, review the upstream license and
   notice requirements and verify the recipe's `LICENSE` and
   `LIC_FILES_CHKSUM` declarations.
5. Review all `CLOSED` or proprietary components separately. Add their
   redistributable license/EULA, copyright, notices, and acknowledgements to
   their Yocto recipe's `LIC_FILES_CHKSUM`. Confirm that the agreement permits
   those texts to be redistributed in the customer delivery.
6. Check applications that download, vendor, or generate dependencies during
   compilation. Such dependencies must either be represented by their own
   recipes or have their legal texts and corresponding source added to the
   parent recipe.
7. Preserve the clearing archive together with the exact binary image and
   release record. Transfer the archive itself to the customer; do not replace
   it with an Internet URL.

This review is required because build automation can report only metadata and
files declared by recipes. It cannot decide license interpretation, detect an
undeclared commercial agreement, or establish that an upstream notice is
legally complete.

## Written offers

This project delivers corresponding source directly and does not generate a
written offer. Do not substitute a written offer unless legal review confirms
that every applicable license permits it and the organization can satisfy the
offer for the entire required period.
