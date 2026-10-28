'use strict';

(() => {
  const groups = [
    {
      label: 'Agenda',
      links: [
        ['agenda-calendrier.html', 'Agenda'],
        ['agenda.html', 'Agenda et parcours patient'],
        ['reservation-en-ligne.html', 'Réservation en ligne']
      ]
    },
    {
      label: 'Scanner et historique',
      links: [
        ['scan.html', 'Scanner QR/OCR'],
        ['historique.html', 'Historique des scans'],
        ['ocr.html', 'Documents OCR (factures)']
      ]
    },
    {
      label: 'Patients',
      links: [
        ['historique-patients.html', 'Historique patient'],
        ['patients.html', 'Fiches patients'],
        ['delivrance.html', 'Nouvelle délivrance']
      ]
    },
    {
      label: 'Gestion du stock',
      links: [
        ['stock.html', 'Inventaire et sorties'],
        ['stock-alertes.html', 'Alertes et seuils'],
        ['stock-qr.html', 'Entrée de stock par QR']
      ]
    },
    {
      label: 'Statistiques',
      links: [
        ['statistiques.html', 'Délivrances par prestataire'],
        ['occupation-salles.html', 'Occupation des salles']
      ]
    },
    {
      label: 'Administration',
      roles: ['admin', 'superadmin'],
      links: [
        ['utilisateurs.html', 'Gestion des utilisateurs'],
        ['medecins.html', 'Médecins et droits'],
        ['superadmin.html', 'Journal SuperAdmin', ['superadmin']]
      ]
    }
  ];
  const menuIdleDelay = 5000;

  const root = document.querySelector('main, .container');
  if (!root) return;

  const navigation = document.createElement('nav');
  navigation.className = 'app-navigation';
  navigation.setAttribute('aria-label', 'Navigation principale');
  const currentPage = window.location.pathname.split('/').pop() || 'index.html';
  const user = window.AppAuth?.user();
  const requiresDispenseLogin = currentPage === 'delivrance.html'
    && new URLSearchParams(window.location.search).has('patient_id')
    && !user;
  const requiresPatientHistoryLogin = currentPage === 'historique-patients.html'
    && new URLSearchParams(window.location.search).has('patient_id')
    && !user;
  const collapseAfterNavigationKey = 'app-nav-collapse-after-navigation';
  const navigationEntry = performance.getEntriesByType('navigation')[0];
  const collapseAfterNavigation = sessionStorage.getItem(collapseAfterNavigationKey) === currentPage;
  const pageWasReloaded = navigationEntry?.type === 'reload';
  const isPatientShortcut = new URLSearchParams(window.location.search).has('patient_id');
  sessionStorage.removeItem(collapseAfterNavigationKey);

  const homeLink = document.createElement('a');
  homeLink.className = 'app-nav-home';
  homeLink.href = 'index.html';
  homeLink.textContent = 'Accueil';
  if (currentPage === 'index.html') homeLink.setAttribute('aria-current', 'page');
  navigation.append(homeLink);

  const agendaPages = ['agenda.html', 'agenda-calendrier.html', 'occupation-salles.html', 'reservation-en-ligne.html'];
  // Sans connexion, le lien reste visible pour mener à l'écran de connexion.
  const showAgenda = !user || window.AppAuth?.canUseAgenda(user);

  // Le droit Agenda peut changer côté SuperAdmin : on resynchronise le profil puis on recharge une fois.
  if (user && window.AppAuth?.refreshUser) {
    window.AppAuth.refreshUser().then((fresh) => {
      if (fresh && window.AppAuth.canUseAgenda(fresh) !== Boolean(showAgenda)) window.location.reload();
    }).catch(() => {});
  }

  const groupMenus = [];
  groups.forEach(({ label, links: allLinks, role, roles }) => {
    if (role && user?.role !== role) return;
    if (roles && !roles.includes(user?.role)) return;
    const links = allLinks
      .filter(([href]) => showAgenda || !agendaPages.includes(href))
      .filter(([, , linkRoles]) => !linkRoles || linkRoles.includes(user?.role));
    if (!links.length) return;
    const groupMenu = document.createElement('details');
    groupMenu.className = 'app-nav-group';
    const summary = document.createElement('summary');
    summary.textContent = label;
    const linkList = document.createElement('div');
    linkList.className = 'app-nav-group-links';

    links.forEach(([href, linkLabel]) => {
      const link = document.createElement('a');
      link.href = href;
      link.textContent = linkLabel;
      if (href === currentPage) {
        link.setAttribute('aria-current', 'page');
        summary.setAttribute('aria-current', 'page');
      }
      link.addEventListener('click', () => {
        sessionStorage.setItem(collapseAfterNavigationKey, href);
      });
      linkList.append(link);
    });

    if (!collapseAfterNavigation && !pageWasReloaded && !isPatientShortcut
      && links.some(([href]) => href === currentPage)) {
      groupMenu.open = true;
    }

    groupMenu.append(summary, linkList);
    groupMenus.push(groupMenu);
    navigation.append(groupMenu);
  });

  groupMenus.forEach((groupMenu) => {
    let idleTimer;
    const clearIdleTimer = () => {
      window.clearTimeout(idleTimer);
    };
    const restartIdleTimer = () => {
      clearIdleTimer();
      if (!groupMenu.open) return;
      idleTimer = window.setTimeout(() => {
        if (!groupMenu.open) return;
        groupMenu.open = false;
        groupMenu.querySelector('summary').focus();
      }, menuIdleDelay);
    };

    groupMenu.addEventListener('toggle', () => {
      if (groupMenu.open) {
        groupMenus.filter((other) => other !== groupMenu).forEach((other) => {
          other.open = false;
        });
        const authMenu = navigation.querySelector('.app-auth-menu');
        if (authMenu) authMenu.open = false;

        requestAnimationFrame(() => {
          const popup = groupMenu.querySelector('.app-nav-group-links');
          popup.style.left = '0';
          popup.style.right = 'auto';
          if (popup.getBoundingClientRect().right > document.documentElement.clientWidth - 10) {
            popup.style.left = 'auto';
            popup.style.right = '0';
          }
        });
        restartIdleTimer();
      } else {
        clearIdleTimer();
      }
    });

    ['pointerenter', 'pointermove', 'pointerdown', 'focusin', 'keydown'].forEach((eventName) => {
      groupMenu.addEventListener(eventName, restartIdleTimer);
    });

    if (groupMenu.open) restartIdleTimer();
  });

  const loginRegion = document.querySelector('.auth-panel, .admin-login, #loginForm');
  if (loginRegion) {
    const authMenu = document.createElement('details');
    authMenu.className = 'app-auth-menu';
    const summary = document.createElement('summary');
    const authPanel = currentPage === 'scan.html'
      ? loginRegion
      : document.createElement('div');
    authPanel.classList.add('app-auth-panel');

    const updateSummary = () => {
      const currentUser = window.AppAuth?.user();
      summary.textContent = currentUser ? `Connecté : ${currentUser.username}` : 'Connexion';
      summary.setAttribute('aria-label', currentUser ? `Compte ${currentUser.username}` : 'Ouvrir la connexion');
    };

    updateSummary();

    if (authPanel !== loginRegion) authPanel.append(loginRegion);
    const loginButton = loginRegion.querySelector('#authLogin, #loginButton');
    const accountRegion = document.querySelector('#authBar, #accountBar');
    if (accountRegion && !loginRegion.contains(accountRegion)) authPanel.append(accountRegion);

    const logoutButton = document.querySelector('#logoutButton');
    if (logoutButton && !loginRegion.contains(logoutButton) && !accountRegion?.contains(logoutButton)) {
      authPanel.append(logoutButton);
    }

    const signedInAs = document.querySelector('#signedInAs');
    if (signedInAs && !loginRegion.contains(signedInAs) && !accountRegion?.contains(signedInAs)) {
      authPanel.append(signedInAs);
    }

    const authStatus = document.querySelector('#authStatus, #authMessage');
    if (authStatus && !loginRegion.contains(authStatus)) authPanel.append(authStatus);
    authMenu.append(summary, authPanel);
    navigation.append(authMenu);

    window.addEventListener('app-auth-login-start', () => {
      loginButton?.classList.remove('auth-login-failed', 'auth-login-success');
      summary.classList.remove('auth-login-success');
    });
    window.addEventListener('app-auth-login-failure', () => {
      loginButton?.classList.add('auth-login-failed');
    });
    window.addEventListener('app-auth-login', () => {
      updateSummary();
      loginButton?.classList.remove('auth-login-failed');
      loginButton?.classList.add('auth-login-success');
      summary.classList.add('auth-login-success');
      authMenu.open = false;
    });
    window.addEventListener('app-auth-logout', () => {
      updateSummary();
      loginButton?.classList.remove('auth-login-failed', 'auth-login-success');
      summary.classList.remove('auth-login-success');
    });
    authMenu.addEventListener('toggle', () => {
      if (authMenu.open) groupMenus.forEach((groupMenu) => { groupMenu.open = false; });
    });

    if (requiresDispenseLogin || requiresPatientHistoryLogin) {
      authMenu.open = true;
      window.requestAnimationFrame(() => {
        loginRegion.querySelector('input')?.focus();
      });
    }
  }

  root.prepend(navigation);
})();
