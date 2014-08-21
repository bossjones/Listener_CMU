"""Qt GUI wrapper"""
import logging,os,json, pprint, math
from . import pipeline, context
from .oneshot import one_shot
try:
    from PySide import (
        QtCore, QtGui, QtWebKit
    )
except ImportError as err:
    from PyQt4 import (
        QtCore, QtGui, QtWebKit
    )
from jinja2 import Environment, FileSystemLoader

HERE = os.path.dirname( __file__ )

TEMPLATE_ENVIRONMENT = Environment( loader=FileSystemLoader(os.path.join( HERE, 'templates')) )
MAIN_PAGE_TEMPLATE = TEMPLATE_ENVIRONMENT.get_template( 'main.html' )

log = logging.getLogger(__name__)

class QtPipelineGenerator( QtCore.QObject ):
    """QObject generating events from the Pipeline"""
    partial = QtCore.Signal(dict)
    final = QtCore.Signal(dict)
    level = QtCore.Signal(dict)

class JavascriptBridge( QtCore.QObject ):
    """A QObject that can process clicks"""
    js_event = QtCore.Signal(dict)
    
    @QtCore.Slot(str)
    def send_event( self, event ):
        log.info( 'Received event from javascript' )
        return self.js_event.emit( json.loads(event) )

class QtPipeline(pipeline.Pipeline):
    """Pipeline that sends messages through Qt Events"""
    @one_shot
    def events( self ):
        return QtPipelineGenerator()
    def send( self, message ):
        event = getattr( self.events, message['type'],None)
        if event:
            event.emit( message )

class ListenerMain( QtGui.QMainWindow ):
    """Main application window for listener"""
    def __init__( self, *args, **named ):
        command_line_arguments = named.pop('arguments',None)
        super( ListenerMain, self ).__init__( *args, **named )
        self.context = context.Context( getattr(
            command_line_arguments,'context','default'
        ) )
        self.pipeline = QtPipeline( self.context )
        self.create_gui()
        self.pipeline.start_listening()
    def create_gui( self ):
        self.setWindowTitle( 'Listener' )
        self.statusBar().showMessage( 'Initializing the context' )
        self.create_menus()
        
        QtWebKit.QWebSettings.globalSettings().setAttribute(
            QtWebKit.QWebSettings.DeveloperExtrasEnabled, True
        )
        self.view = QtWebKit.QWebView(self)
        self.view_frame.baseURL = 'file://'+os.path.abspath(os.path.join( HERE ))
        self.view.setHtml( self.main_view_html() )
        
        self.main_html = self.element_by_selector( 'div.main-view' )
        self.final_results = self.element_by_selector( '.final-results' )
        
        self.view_frame.javaScriptWindowObjectCleared.connect(
            self.add_gui_bridge
        )
        
        self.setCentralWidget( self.view )
        
        self.view.show()
        
        self.pipeline.events.partial.connect( self.on_partial )
        self.pipeline.events.final.connect( self.on_final )
        self.pipeline.events.level.connect( self.on_level )
    
    @property
    def view_frame( self ):
        return self.view.page().mainFrame()
    def elements_by_selector( self, selector ):
        return self.view_frame.findAllElements( selector )
    def element_by_selector( self, selector ):
        return self.view_frame.findFirstElement( selector )
        
    def main_view_html( self ):
        return MAIN_PAGE_TEMPLATE.render( 
            view = self,
            HERE = os.path.abspath( HERE ),
        )
    def quit( self, *args ):
        self.pipeline.close()
        QtGui.qApp.quit()
    def create_menus( self ):
        menubar = self.menuBar()
        fileMenu = menubar.addMenu('&File')
        
        chooseAction = QtGui.QAction('&Microphone', self)
        chooseAction.setStatusTip('Choose the ALSA microphone to use')
        chooseAction.triggered.connect(self.on_choose_input)
        fileMenu.addAction(chooseAction)
        
        chooseAction = QtGui.QAction('&Speaker', self)
        chooseAction.setStatusTip('Choose the ALSA speaker to use')
        chooseAction.triggered.connect(self.on_choose_output)
        fileMenu.addAction(chooseAction)
        
        exitAction = QtGui.QAction(QtGui.QIcon('exit.png'), '&Exit', self)        
        exitAction.setShortcut('Alt-F4')
        exitAction.setStatusTip('Exit application')
        exitAction.triggered.connect(self.quit)
        fileMenu.addAction(exitAction)
    
    ZERO_LEVEL_AUDIO = math.log( 60 )
    FULL_LEVEL_AUDIO = math.log( 20 )
    def on_level( self, record ):
        """Interpret recording level in manner useful to user...
        
        Really need to get this to be a useful tool; basically it 
        *seems* like the pocketsphinx stuff works fine with full-volume
        input, but in noisy environments the vader won't pick up the 
        end of the utterance
        """
        intensity = math.log( abs(record['level']))
        intensity = (intensity - self.ZERO_LEVEL_AUDIO)/(self.FULL_LEVEL_AUDIO-self.ZERO_LEVEL_AUDIO)
        translated = min((1.0,max((0,intensity))))
        js = 'recording_level( %f )'%(translated,)
        self.view_frame.evaluateJavaScript(
            js
        )
    
    def on_partial( self, record ):
        self.statusBar().showMessage( record['text'] )
    def on_final( self, record ):
        js = '''add_final( %s );'''%(json.dumps( record ))
        self.view_frame.evaluateJavaScript(
            js
        )
#        self.final_results.appendInside(
#            '''<li class="final-result">%s</li>'''%(cgi.escape( record['text']) )
#        )
#        element = self.final_results.lastChild()
    @QtCore.Slot()
    def add_gui_bridge( self ):
        self.bridge = JavascriptBridge()
        self.bridge.js_event.connect( self.on_js_event )
        self.view_frame.addToJavaScriptWindowObject(
            "gui_bridge",
            self.bridge,
        )
    @QtCore.Slot()
    def on_js_event( self, event ):
        log.info( 'Received event from javascript: %s', event )
        if event['action'] == 'listen':
            record = event['record']
            if record['files']:
                for file in record['files']:
                    log.info( 'Playing file %s', file )
                    self.context.rawplay( file )
            else:
                log.error( 'No files were present: %s', pprint.pformat(event))
        else:
            log.info( 'Unrecognized action: %s', pprint.pformat( event ))
    
    def on_choose_input( self, event=None ):
        def update_input( choice ):
            self.pipeline.stop_listening()
            self.pipeline._source = None
            self.pipeline.pipeline.get_by_name( 
                'source' 
            ).set_property(
                'device',choice
            )
            self.pipeline.start_listening()
        return self.on_choose_alsa_device( 'input', update_input )
    def on_choose_output( self, event=None ):
        return self.on_choose_alsa_device( 'output', None )
    
    def on_choose_alsa_device( self, key='input', updater=None ):
        current = self.context.audio_context().settings['%s_device'%(key,)]
        choices = self.context.available_alsa_devices()[key]
        current_index = 0
        for i,(label,name) in enumerate(choices):
            if name == current:
                current_index = i
        if key == 'input':
            title,label = "Choose Input Microphone", "ALSA Microphone"
        else:
            title,label = "Choose Output Speaker", "ALSA Speaker"
        item,ok = QtGui.QInputDialog.getItem(
            self,
            title,
            label,
            [label for label,name in choices],
            current=current_index,
            editable=False,
            ok=True,
        )
        if ok:
            choice = None
            for label,name in choices:
                if item == label:
                    if name != current:
                        choice = name
                        log.info( 'Chose device: %s (%s)', label, name )
                        self.context.audio_context().update_settings({
                            '%s_device'%(key,): choice,
                        })
                        if updater:
                            updater( choice )
                    else:
                        log.info( 'Chose the current device, ignoring' )
