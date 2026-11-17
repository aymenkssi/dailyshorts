// Demande de confirmation pour les actions irréversibles (data-confirm sur le formulaire)
document.addEventListener("submit", function (e) {
  var msg = e.target.getAttribute("data-confirm");
  if (msg && !window.confirm(msg)) e.preventDefault();
});

// Bouton « Copier » : copie le contenu du champ indiqué par data-copy
document.addEventListener("click", function (e) {
  var id = e.target.getAttribute && e.target.getAttribute("data-copy");
  if (!id) return;
  var field = document.getElementById(id);
  var done = function () { e.target.textContent = "Copié ✓"; setTimeout(function () { e.target.textContent = "Copier la légende"; }, 2000); };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(field.value).then(done, function () { field.select(); document.execCommand("copy"); done(); });
  } else { field.select(); document.execCommand("copy"); done(); }
});
