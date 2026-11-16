// Demande de confirmation pour les actions irréversibles (data-confirm sur le formulaire)
document.addEventListener("submit", function (e) {
  var msg = e.target.getAttribute("data-confirm");
  if (msg && !window.confirm(msg)) e.preventDefault();
});
