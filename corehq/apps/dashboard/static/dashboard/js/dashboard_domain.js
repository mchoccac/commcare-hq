hqDefine('dashboard/js/dashboard_domain.js', function() {
    'use strict';
    var initial_page_data = hqImport('hqwebapp/js/initial_page_data.js').get;
    var dashboardApp = window.angular.module('dashboardApp', [
        'ui.bootstrap',
        'ngResource',
        'ngRoute',
        'ng.django.rmi',
        'hq.dashboard',
    ]);
    dashboardApp.config(['$httpProvider', function($httpProvider) {
        $httpProvider.defaults.headers.common['X-Requested-With'] = 'XMLHttpRequest';
        $httpProvider.defaults.xsrfCookieName = 'csrftoken';
        $httpProvider.defaults.xsrfHeaderName = 'X-CSRFToken';
    }]);
    dashboardApp.config(["djangoRMIProvider", function(djangoRMIProvider) {
        djangoRMIProvider.configure(initial_page_data('djng_current_rmi'));
    }]);
    dashboardApp.constant('dashboardConfig', {
        staticRoot: initial_page_data('static_url'),
    });
});
