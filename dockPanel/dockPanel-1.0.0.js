/**
 * jQuery docking panel, under MIT License
 * ----------------------
 * @author oohacker
 * @email oohacker@qq.com
 * @version 1.0.0
 * @date: 2019/03/01
 */
(function($){

    var defaultSettings = {
        dock: 'right'
    };
    

    var _initPanel = (elements, settings)=>{
        var dock = settings.dock;
        if(dock == 'left' || dock == 'right') {
            $.each(elements, function(index, el) {
                var width = $(el).width();
                $(el).css({
                    position: 'fixed',
                    overflowX: 'hidden',
                    [dock]: (-width) + 'px',
                    top:0,
                    bottom:0,
                    width: width + 'px'
                });
            });
        }
        else {
            $.each(this, function(index, el) {
                var height = $(el).height();
                $(el).css({
                    position: 'fixed',
                    overflowY: 'hidden',
                    left: 0,
                    right: 0,
                    [dock]:(-height)+'px'
                });
            });
        }
        
        $.each(elements, (i,val)=>{
            $(val).data('docking', settings.dock);
        });
    }


    var _showPanel = (el, callback) => {
        console.log('show');
        $(el).show();
        var docking = $(el).data('docking');
        $(el).animate({[docking]: '0px'}, 'fast', callback);
    };

    var _hidePanel = (el, callback) => {
        console.log('hide');
        var docking = $(el).data('docking');
        var width = $(el).width();
        var height = $(el).height();
        if(docking == 'left' || docking == 'right') {
            $(el).animate({[docking]: (-width) + 'px'}, 'fast', callback);
        }
        else {
            $(el).animate({[docking]: (-height) + '0px'}, 'fast', callback);
        }
    };


    $.fn.dockPanel = function() {

        if(arguments.length == 0 || typeof(arguments[0])=='object') {
            var settings = $.extend({}, defaultSettings, arguments[0]);
            _initPanel(this, settings);
        }
        else if(arguments.length>0 && typeof(arguments[0])=='string') {
            var callback = arguments.length>1 && typeof(arguments[1]=='function') ? arguments[1] : ()=>{};
            $.each(this, (i, el)=>{
                switch(arguments[0]) {
                    case 'show': 
                        _showPanel($(el), callback);
                        break;
                    case 'hide':
                        _hidePanel($(el), callback);
                        break;
                }
            });
        }

        //console.log('docking panel inited');
    }

    
})(jQuery);