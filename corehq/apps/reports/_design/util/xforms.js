/*
 * This module has utilities for working with xforms
 */


/*
 * use this to make sure you loaded the reports module properly
 */
function hello_xforms() {
    log("hello xforms!");
}

function xform_matches(doc, namespace) {
    return doc.doc_type == "XFormInstance" && doc.xmlns == namespace;
}

var exists = function(basestring, searchstring) {
    try {
        return basestring && basestring.indexOf(searchstring) >= 0;
    } catch(err) {
        // oops.  this might not have been a string.
        log("There's a problem checking for " + searchstring + " in " + basestring + ". The searched string is likely not a string");
        return false;
    }
}

function get_date_string(xform_doc) {
	// check some expected places for a date
    var form = xform_doc.form;
    var meta = form.Meta;
	if (form.encounter_date) return form.encounter_date;
	if (form.date) return form.date;
    if (meta && meta.TimeEnd) return meta.TimeEnd;
	if (meta && meta.TimeStart) return meta.TimeStart;
	return null;
}

// parse a date in yyyy-mm-dd format
function parse_date(date_string) {
    // hat tip: http://stackoverflow.com/questions/2587345/javascript-date-parse
    if (date_string) {
	    var parts = date_string.match(/(\d+)/g);
	    // new Date(year, month [, date [, hours[, minutes[, seconds[, ms]]]]])
	    // For some reason using "new Date" fails to produce correct output
        // but just using "Date" works...
	    return Date(parts[0], parts[1]-1, parts[2]); // months are 0-based
    }
}

function get_encounter_date(xform_doc) {
    date_str = get_date_string(xform_doc);
    if (date_str) {
        return parse_date(date_str);
    }
    return null;
}

function get_form_filled_duration(xform_doc) {
    // in milliseconds
    var meta = xform.form.Meta;
    if (meta && meta.TimeEnd && meta.TimeStart)
        return new Date(meta.TimeEnd).getTime() - new Date(meta.TimeStart).getTime();
    return null;
}

function get_form_filled_date(xform_doc) {
    var meta = xform.form.Meta;
    if (meta && meta.TimeEnd) return new Date(meta.TimeEnd);
    if (meta && meta.TimeStart) return new Date(meta.TimeStart);
    return null;
}