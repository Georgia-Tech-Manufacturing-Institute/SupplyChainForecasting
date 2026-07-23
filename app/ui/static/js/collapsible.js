/* Makes helper-sidebar description boxes collapsible: clicking a .desc-title
   header toggles visibility of the content that follows it. */
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.desc-title').forEach(function (title) {
        const siblings = [];
        let el = title.nextElementSibling;
        while (el) {
            siblings.push(el);
            el = el.nextElementSibling;
        }
        if (!siblings.length) return;

        title.classList.add('collapsible');
        title.addEventListener('click', function () {
            const collapsed = title.classList.toggle('collapsed');
            siblings.forEach(function (s) {
                s.style.display = collapsed ? 'none' : '';
            });
            if (title.parentElement) {
                title.parentElement.classList.toggle('box-collapsed', collapsed);
            }
        });
    });
});
